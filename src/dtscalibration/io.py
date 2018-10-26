# coding=utf-8
import os

import dask.array as da
import numpy as np
import pandas as pd

# Returns a dictionary with the attributes to the dimensions. The keys refer to the namin
#     gin used in the raw files.
_dim_attrs = {
    ('x', 'distance'):          {
        'name':             'distance',
        'description':      'Length along fiber',
        'long_describtion': 'Starting at connector of forward channel',
        'units':            'm'},
    ('TMP', 'temperature'):     {
        'name':        'TMP',
        'description': 'temperature calibrated by device',
        'units':       'degC'},
    ('ST',):                    {
        'name':        'ST',
        'description': 'Stokes intensity',
        'units':       '-'},
    ('AST',):                   {
        'name':        'AST',
        'description': 'anti-Stokes intensity',
        'units':       '-'},
    ('REV-ST',):                {
        'name':        'REV-ST',
        'description': 'reverse Stokes intensity',
        'units':       '-'},
    ('REV-AST',):               {
        'name':        'REV-AST',
        'description': 'reverse anti-Stokes intensity',
        'units':       '-'},
    ('acquisitionTime',):       {
        'name':             'acquisitionTime',
        'description':      'Measurement duration of forward channel',
        'long_describtion': 'Actual measurement duration of forward channel',
        'units':            'seconds'},
    ('userAcquisitionTimeFW',): {
        'name':             'userAcquisitionTimeFW',
        'description':      'Measurement duration of forward channel',
        'long_describtion': 'Desired measurement duration of forward channel',
        'units':            'seconds'},
    ('userAcquisitionTimeBW',): {
        'name':             'userAcquisitionTimeBW',
        'description':      'Measurement duration of backward channel',
        'long_describtion': 'Desired measurement duration of backward channel',
        'units':            'seconds'},
    }

# Because variations in the names exist between the different file
#     formats. The tuple as key contains the possible keys, which is expanded below.
dim_attrs = {k: v for kl, v in _dim_attrs.items() for k in kl}


def silixa_xml_version_check(filepathlist):
    """Function which tests which version of xml files have to be read.

    Parameters
    ----------
    filepathlist

    Returns
    -------

    """

    sep = ':'
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    version_string = attrs['customData:SystemSettings:softwareVersion']

    # Get major version from string. Tested for Ultima v4, v6, XT-DTS v6
    major_version = int(version_string.replace(' ', '').split(':')[-1][0])

    return major_version


def read_silixa_files_routine_v6(filepathlist,
                                 timezone_netcdf='UTC',
                                 timezone_ultima_xml='UTC',
                                 silent=False,
                                 load_in_memory='auto'):
    """
    Internal routine that reads Silixa files. Use dtscalibration.read_silixa_files function instead.

    Parameters
    ----------
    filepathlist
    timezone_netcdf
    timezone_ultima_xml
    silent

    Returns
    -------

    """
    import dask
    from xml.etree import ElementTree

    sep = ':'
    ns = {'s': 'http://www.witsml.org/schemas/1series'}

    # Obtain metadata from the first file
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    # obtain basic data info
    data_item_names = attrs['logData:mnemonicList'].replace(" ", "").strip(' ').split(',')
    nitem = len(data_item_names)

    x_start = np.float32(attrs['startIndex:#text'])
    x_end = np.float32(attrs['endIndex:#text'])
    dx = np.float32(attrs['stepIncrement:#text'])
    nx = int((x_end - x_start) / dx)

    ntime = len(filepathlist)

    double_ended_flag = bool(int(attrs['customData:isDoubleEnded']))
    chFW = int(attrs['customData:forwardMeasurementChannel']) - 1  # zero-based
    if double_ended_flag:
        chBW = int(attrs['customData:reverseMeasurementChannel']) - 1  # zero-based
    else:
        # no backward channel is negative value. writes better to netcdf
        chBW = -1

    # print summary
    if not silent:
        print('%s files were found, each representing a single timestep' % ntime)
        print('%s recorded vars were found: ' % nitem + ', '.join(data_item_names))
        print('Recorded at %s points along the cable' % nx)

        if double_ended_flag:
            print('The measurement is double ended')
        else:
            print('The measurement is single ended')

    # obtain timeseries from data
    timeseries_loc_in_hierarchy = [
        ('log', 'customData', 'acquisitionTime'),
        ('log', 'customData', 'referenceTemperature'),
        ('log', 'customData', 'probe1Temperature'),
        ('log', 'customData', 'probe2Temperature'),
        ('log', 'customData', 'referenceProbeVoltage'),
        ('log', 'customData', 'probe1Voltage'),
        ('log', 'customData', 'probe2Voltage'),
        ('log', 'customData', 'UserConfiguration',
         'ChannelConfiguration', 'AcquisitionConfiguration',
         'AcquisitionTime', 'userAcquisitionTimeFW')
        ]

    if double_ended_flag:
        timeseries_loc_in_hierarchy.append(
            ('log', 'customData', 'UserConfiguration',
             'ChannelConfiguration', 'AcquisitionConfiguration',
             'AcquisitionTime', 'userAcquisitionTimeBW'))

    timeseries = {
        item[-1]: dict(loc=item, array=np.zeros(ntime, dtype=np.float32))
        for item in timeseries_loc_in_hierarchy
        }

    # add units to timeseries (unit of measurement)
    for key, item in timeseries.items():
        if f'customData:{key}:uom' in attrs:
            item['uom'] = attrs[f'customData:{key}:uom']
        else:
            item['uom'] = ''

    # Gather data
    arr_path = 's:' + '/s:'.join(['log', 'logData', 'data'])

    @dask.delayed
    def grab_data_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)
            arr_el = eltree.findall(arr_path, namespaces=ns)

            # remove the breaks on both sides of the string
            # split the string on the comma
            arr_str = [arr_eli.text[1:-1].split(',') for arr_eli in arr_el]

        return np.array(arr_str, dtype=np.float)

    data_lst_dly = [grab_data_per_file(fp) for fp in filepathlist]
    data_lst = [da.from_delayed(x, shape=(nx, nitem), dtype=np.float) for x in data_lst_dly]
    data_arr = da.stack(data_lst).T  # .compute()

    # Check whether to compute data_arr (if possible 25% faster)
    data_arr_cnk = data_arr.rechunk({0: -1, 1: -1, 2: 'auto'})
    if load_in_memory == 'auto' and data_arr_cnk.npartitions <= 5:
        data_arr = data_arr_cnk.compute()
    elif load_in_memory:
        data_arr = data_arr_cnk.compute()
    else:
        data_arr = data_arr_cnk

    data_vars = {}
    for name, data_arri in zip(data_item_names, data_arr):
        if name == 'LAF':
            continue

        if name in dim_attrs:
            data_vars[name] = (['x', 'time'], data_arri, dim_attrs[name])

        else:
            raise ValueError('Dont know what to do with the {} data column'.format(name))

    # Obtaining the timeseries data (reference temperature etc)
    _ts_dtype = [(k, np.float32) for k in timeseries]
    _time_dtype = [('filename_tstamp', np.int64),
                   ('minDateTimeIndex', '<U29'),
                   ('maxDateTimeIndex', '<U29')]
    ts_dtype = np.dtype(_ts_dtype + _time_dtype)

    @dask.delayed
    def grab_timeseries_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)

            out = []
            for k, v in timeseries.items():
                # Get all the timeseries data
                if 'userAcquisitionTimeFW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chFW].find(path2, namespaces=ns)
                    out.append(val2.text)

                elif 'userAcquisitionTimeBW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chBW].find(path2, namespaces=ns)
                    out.append(val2.text)

                else:
                    path = 's:' + '/s:'.join(v['loc'])
                    val = eltree.find(path, namespaces=ns)
                    out.append(val.text)

            # get all the time related data
            startDateTimeIndex = eltree.find(
                's:log/s:startDateTimeIndex', namespaces=ns).text
            endDateTimeIndex = eltree.find(
                's:log/s:endDateTimeIndex', namespaces=ns).text

            file_name = os.path.split(file_handle)[1]
            tstamp = np.int64(file_name[10:27])

            out += [tstamp, startDateTimeIndex, endDateTimeIndex]
        return np.array(tuple(out), dtype=ts_dtype)

    ts_lst_dly = [grab_timeseries_per_file(fp) for fp in filepathlist]
    ts_lst = [da.from_delayed(x, shape=tuple(), dtype=ts_dtype) for x in ts_lst_dly]
    ts_arr = da.stack(ts_lst).compute()

    for name in timeseries:
        if name in dim_attrs:
            data_vars[name] = (('time',), ts_arr[name], dim_attrs[name])

        else:
            data_vars[name] = (('time',), ts_arr[name])

    # construct the coordinate dictionary
    coords = {
        'x':        ('x', data_arr[0, :, 0], dim_attrs['x']),
        'filename': ('time', [os.path.split(f)[1] for f in filepathlist]),
        'filename_tstamp': ('time', ts_arr['filename_tstamp'])}

    maxTimeIndex = pd.DatetimeIndex(ts_arr['maxDateTimeIndex'])
    dtFW = ts_arr['userAcquisitionTimeFW'].astype('timedelta64[s]')

    if not double_ended_flag:
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_ultima_xml,
                              dtFW=dtFW, double_ended_flag=double_ended_flag)
    else:
        dtBW = ts_arr['userAcquisitionTimeBW'].astype('timedelta64[s]')
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_ultima_xml,
                              dtFW=dtFW, dtBW=dtBW, double_ended_flag=double_ended_flag)

    coords.update(tcoords)

    return data_vars, coords, attrs


def read_silixa_files_routine_v4(filepathlist,
                                 timezone_netcdf='UTC',
                                 timezone_ultima_xml='UTC',
                                 silent=False,
                                 load_in_memory='auto'):
    """
    Internal routine that reads Silixa files. Use dtscalibration.read_silixa_files function instead.

    Parameters
    ----------
    filepathlist
    timezone_netcdf
    timezone_ultima_xml
    silent

    Returns
    -------

    """
    import dask
    from xml.etree import ElementTree

    sep = ':'
    ns = {'s': 'http://www.witsml.org/schemas/1series'}

    # Obtain metadata from the first file
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    double_ended_flag = bool(int(attrs['customData:isDoubleEnded']))
    chFW = int(attrs['customData:forwardMeasurementChannel']) - 1  # zero-based
    if double_ended_flag:
        chBW = int(attrs['customData:reverseMeasurementChannel']) - 1  # zero-based
    else:
        # no backward channel is negative value. writes better to netcdf
        chBW = -1

    # obtain basic data info
    if double_ended_flag:
        data_item_names = [attrs['logCurveInfo_{0}:mnemonic'.format(x)] for x in range(0, 6)]
    else:
        data_item_names = [attrs['logCurveInfo_{0}:mnemonic'.format(x)] for x in range(0, 4)]

    nitem = len(data_item_names)

    x_start = np.float32(attrs['blockInfo:startIndex:#text'])
    x_end = np.float32(attrs['blockInfo:endIndex:#text'])
    dx = np.float32(attrs['blockInfo:stepIncrement:#text'])
    nx = int((x_end - x_start) / dx)

    ntime = len(filepathlist)

    # print summary
    if not silent:
        print('%s files were found, each representing a single timestep' % ntime)
        print('%s recorded vars were found: ' % nitem + ', '.join(data_item_names))
        print('Recorded at %s points along the cable' % nx)

        if double_ended_flag:
            print('The measurement is double ended')
        else:
            print('The measurement is single ended')

    # obtain timeseries from data
    timeseries_loc_in_hierarchy = [
        ('wellLog', 'customData', 'acquisitionTime'),
        ('wellLog', 'customData', 'referenceTemperature'),
        ('wellLog', 'customData', 'probe1Temperature'),
        ('wellLog', 'customData', 'probe2Temperature'),
        ('wellLog', 'customData', 'referenceProbeVoltage'),
        ('wellLog', 'customData', 'probe1Voltage'),
        ('wellLog', 'customData', 'probe2Voltage'),
        ('wellLog', 'customData', 'UserConfiguration',
         'ChannelConfiguration', 'AcquisitionConfiguration',
         'AcquisitionTime', 'userAcquisitionTimeFW')
        ]

    if double_ended_flag:
        timeseries_loc_in_hierarchy.append(
            ('wellLog', 'customData', 'UserConfiguration',
             'ChannelConfiguration', 'AcquisitionConfiguration',
             'AcquisitionTime', 'userAcquisitionTimeBW'))

    timeseries = {
        item[-1]: dict(loc=item, array=np.zeros(ntime, dtype=np.float32))
        for item in timeseries_loc_in_hierarchy
        }

    # add units to timeseries (unit of measurement)
    for key, item in timeseries.items():
        if f'customData:{key}:uom' in attrs:
            item['uom'] = attrs[f'customData:{key}:uom']
        else:
            item['uom'] = ''

    # Gather data
    arr_path = 's:' + '/s:'.join(['wellLog', 'logData', 'data'])

    @dask.delayed
    def grab_data_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)
            arr_el = eltree.findall(arr_path, namespaces=ns)

            # remove the breaks on both sides of the string
            # split the string on the comma
            arr_str = [arr_eli.text.split(',') for arr_eli in arr_el]
        return np.array(arr_str, dtype=float)

    data_lst_dly = [grab_data_per_file(fp) for fp in filepathlist]
    data_lst = [da.from_delayed(x, shape=(nx, nitem), dtype=np.float) for x in data_lst_dly]
    data_arr = da.stack(data_lst).T  # .compute()

    # Check whether to compute data_arr (if possible 25% faster)
    data_arr_cnk = data_arr.rechunk({0: -1, 1: -1, 2: 'auto'})
    if load_in_memory == 'auto' and data_arr_cnk.npartitions <= 5:
        data_arr = data_arr_cnk.compute()
    elif load_in_memory:
        data_arr = data_arr_cnk.compute()
    else:
        data_arr = data_arr_cnk

    data_vars = {}
    for name, data_arri in zip(data_item_names, data_arr):
        if name == 'LAF':
            continue

        if name in dim_attrs:
            data_vars[name] = (['x', 'time'], data_arri, dim_attrs[name])

        else:
            raise ValueError('Dont know what to do with the {} data column'.format(name))

    # Obtaining the timeseries data (reference temperature etc)
    _ts_dtype = [(k, np.float32) for k in timeseries]
    _time_dtype = [('filename_tstamp', np.int64),
                   ('minDateTimeIndex', '<U29'),
                   ('maxDateTimeIndex', '<U29')]
    ts_dtype = np.dtype(_ts_dtype + _time_dtype)

    @dask.delayed
    def grab_timeseries_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)

            out = []
            for k, v in timeseries.items():
                # Get all the timeseries data
                if 'userAcquisitionTimeFW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chFW].find(path2, namespaces=ns)
                    out.append(val2.text)

                elif 'userAcquisitionTimeBW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chBW].find(path2, namespaces=ns)
                    out.append(val2.text)

                else:
                    path = 's:' + '/s:'.join(v['loc'])
                    val = eltree.find(path, namespaces=ns)
                    out.append(val.text)

            # get all the time related data
            startDateTimeIndex = eltree.find(
                's:wellLog/s:minDateTimeIndex', namespaces=ns).text
            endDateTimeIndex = eltree.find(
                's:wellLog/s:maxDateTimeIndex', namespaces=ns).text

            file_name = os.path.split(file_handle)[1]
            tstamp = np.int64(file_name[10:-4])

            out += [tstamp, startDateTimeIndex, endDateTimeIndex]
        return np.array(tuple(out), dtype=ts_dtype)

    ts_lst_dly = [grab_timeseries_per_file(fp) for fp in filepathlist]
    ts_lst = [da.from_delayed(x, shape=tuple(), dtype=ts_dtype) for x in ts_lst_dly]
    ts_arr = da.stack(ts_lst).compute()

    for name in timeseries:
        if name in dim_attrs:
            data_vars[name] = (('time',), ts_arr[name], dim_attrs[name])

        else:
            data_vars[name] = (('time',), ts_arr[name])

    # construct the coordinate dictionary
    coords = {
        'x':        ('x', data_arr[0, :, 0], dim_attrs['x']),
        'filename': ('time', [os.path.split(f)[1] for f in filepathlist]),
        'filename_tstamp': ('time', ts_arr['filename_tstamp'])}

    maxTimeIndex = pd.DatetimeIndex(ts_arr['maxDateTimeIndex'])
    dtFW = ts_arr['userAcquisitionTimeFW'].astype('timedelta64[s]')

    if not double_ended_flag:
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_ultima_xml,
                              dtFW=dtFW, double_ended_flag=double_ended_flag)
    else:
        dtBW = ts_arr['userAcquisitionTimeBW'].astype('timedelta64[s]')
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_ultima_xml,
                              dtFW=dtFW, dtBW=dtBW, double_ended_flag=double_ended_flag)

    coords.update(tcoords)

    return data_vars, coords, attrs


def read_silixa_attrs_singlefile(filename, sep):
    import xmltodict

    def metakey(meta, dict_to_parse, prefix, sep):
        """
        Fills the metadata dictionairy with data from dict_to_parse. The dict_to_parse is the raw
        data from a silixa xml-file. dict_to_parse is a nested dictionary to represent the
        different levels of hierarchy. For example, toplevel = {lowlevel: {key: value}} . This
        function returns {'toplevel:lowlevel:key': value}. where prefix is the flattened
        hierarchy.

        Parameters
        ----------
        meta : dict
            the output dictionairy with prcessed metadata
        dict_to_parse : dict

        prefix
        sep

        Returns
        -------

        """

        for key in dict_to_parse:
            if prefix == "":

                prefix_parse = key.replace('@', '')
            else:
                prefix_parse = sep.join([prefix, key.replace('@', '')])

            if prefix_parse == sep.join(('logData', 'data')):  # u'logData:data':
                # skip the LAF , ST data
                continue

            if hasattr(dict_to_parse[key], 'keys'):
                # Nested dictionaries, flatten hierarchy.
                meta.update(metakey(meta, dict_to_parse[key], prefix_parse, sep))

            elif isinstance(dict_to_parse[key], list):
                # if the key has values for the multiple channels
                for ival, val in enumerate(dict_to_parse[key]):
                    num_key = prefix_parse + '_' + str(ival)
                    meta.update(metakey(meta, val, num_key, sep))
            else:

                meta[prefix_parse] = dict_to_parse[key]

        return meta

    with open(filename) as fh:
        doc_ = xmltodict.parse(fh.read())

    if u'wellLogs' in doc_.keys():
        doc = doc_[u'wellLogs'][u'wellLog']
    else:
        doc = doc_[u'logs'][u'log']

    return metakey(dict(), doc, '', sep)


def coords_time(maxTimeIndex, timezone_netcdf, timezone_ultima_xml, dtFW=None,
                dtBW=None, double_ended_flag=False):
    time_attrs = {
        'time':        {
            'description': 'time halfway the measurement',
            'timezone':    str(timezone_netcdf)},
        'timestart':   {
            'description': 'time start of the measurement',
            'timezone':    str(timezone_netcdf)},
        'timeend':     {
            'description': 'time end of the measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFW':      {
            'description': 'time halfway the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFWstart': {
            'description': 'time start of the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFWend':   {
            'description': 'time end of the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBW':      {
            'description': 'time halfway the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBWstart': {
            'description': 'time start of the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBWend':   {
            'description': 'time end of the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        }

    if not double_ended_flag:
        # single ended measurement
        dt1 = dtFW.astype('timedelta64[s]')

        # start of the forward measurement
        index_time_FWstart = maxTimeIndex - dt1

        # end of the forward measurement
        index_time_FWend = maxTimeIndex

        # center of forward measurement
        index_time_FWmean = maxTimeIndex - dt1 / 2

        coords_zip = [('timestart', index_time_FWstart),
                      ('timeend', index_time_FWend),
                      ('time', index_time_FWmean)]

    else:
        # double ended measurement
        dt1 = dtFW.astype('timedelta64[s]')
        dt2 = dtBW.astype('timedelta64[s]')

        # start of the forward measurement
        index_time_FWstart = maxTimeIndex - dt1

        # end of the forward measurement
        index_time_FWend = maxTimeIndex

        # center of forward measurement
        index_time_FWmean = maxTimeIndex - dt1 / 2

        # start of the backward measurement
        index_time_BWstart = index_time_FWend.copy()

        # end of the backward measurement
        index_time_BWend = maxTimeIndex + dt2

        # center of backward measurement
        index_time_BWmean = maxTimeIndex + dt2 / 2

        coords_zip = [('timeFWstart', index_time_FWstart),
                      ('timeFWend', index_time_FWend),
                      ('timeFW', index_time_FWmean),
                      ('timeBWstart', index_time_BWstart),
                      ('timeBWend', index_time_BWend),
                      ('timeBW', index_time_BWmean),
                      ('timestart', index_time_FWstart),
                      ('timeend', index_time_BWend),
                      ('time', index_time_FWend)]

    coords = {k: (
        'time',
        pd.DatetimeIndex(v).tz_localize(
            tz=timezone_ultima_xml).tz_convert(
            timezone_netcdf).astype('datetime64[ns]'),
        time_attrs[k]) for k, v in coords_zip
        }

    return coords