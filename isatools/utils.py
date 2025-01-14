# -*- coding: utf-8 -*-
"""Various utility functions for ISA-API."""
from __future__ import absolute_import
import csv
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from functools import reduce
from zipfile import ZipFile

import pandas as pd

from mzml2isa.mzml import MzMLFile

from isatools import isatab
from isatools.create import create_from_galaxy_parameters
from isatools.model import (
    DerivedSpectralDataFile,
    ISAModelAttributeError,
    OntologyAnnotation,
    ParameterValue,
    Process,
    Protocol,
    plink,
)


log = logging.getLogger('isatools')


def format_report_csv(report):
    """Format JSON validation report as CSV string

    :param report: JSON report output from validator
    :return: string representing csv formatted report
    """
    output = ''

    if report['validation_finished']:
        output = 'Validation=success\n'

    for warning in report['warnings']:
        output += str('{},{},{}\n').format(warning['code'], warning['message'],
                                           warning['supplemental'])

    for error in report['errors']:
        output += str('{},{},{}\n').format(error['code'], error['message'],
                                           error['supplemental'])
    return output


def detect_graph_process_pooling(G):
    """Computes whether there is pooling in an experimental graph. This is
    unexpected in some cases so this function is used to flag these cases.

    :param G: DiGraph of the experimental graph
    :return: List of process IDs on which processes are pooling
    """
    report = []

    for process in [n for n in G.nodes() if isinstance(n, Process)]:
        if len(G.in_edges(process)) > 1:
            log.info('Possible process pooling detected on: {}'
                     .format(' '.join(
                         [process.id, process.executes_protocol.name])))

            report.append(process.id)
    return report


def detect_isatab_process_pooling(fp):
    """Runs the graph process pooling checks on an ISA-Tab

    :param fp: A file-like buffer object pointing to an investigation file
    :return: Report on which files containing process pooling and the
    process IDs inside those tables
    """
    report = []

    ISA = isatab.load(fp)

    for study in ISA.studies:
        log.info('Checking {}'.format(study.filename))
        pooling_list = detect_graph_process_pooling(study.graph)

        if len(pooling_list) > 0:
            report.append({
                study.filename: pooling_list
            })

        for assay in study.assays:
            log.info('Checking {}'.format(assay.filename))
            pooling_list = detect_graph_process_pooling(assay.graph)

            if len(pooling_list) > 0:
                report.append({
                    assay.filename: pooling_list
                })
    return report


def insert_distinct_parameter(table_fp, protocol_ref_to_unpool):
    """A curation function to fix pooling problems

    :param table_fp: A file-like buffer object pointing to a table file
    :param protocol_ref_to_unpool: Reference to which protocol REF column to
    'unpool'
    :return: None
    """
    reader = csv.reader(table_fp, dialect='excel-tab')
    headers = next(reader)  # get column headings
    table_fp.seek(0)

    df = isatab.load_table(table_fp)

    # find protocol ref column by index
    protocol_ref_indices = [x for x, y in enumerate(df.columns) if
                            df[y][0] == protocol_ref_to_unpool]

    if len(protocol_ref_indices) != 1:
        raise IndexError(
            'Could not find Protocol REF with provided value {}'.format(
                protocol_ref_to_unpool))
    distindex = []

    for i in range(0, len(df.index)):
        distindex.append(str(uuid.uuid4())[:8])

    protocol_ref_index = protocol_ref_indices[0]
    name_header = None
    head_from_prot = headers[protocol_ref_index:]

    for x, y in enumerate(head_from_prot):
        if y.endswith(' Name'):
            name_header = y
            break

    if name_header is not None:
        print('Are you sure you want to add a column of hash values in {}? '
              'Y/(N)'.format(name_header))
        confirm = input()
        if confirm == 'Y':
            df[name_header] = distindex
            table_fp.seek(0)
            df.to_csv(table_fp, index=None, header=headers, sep='\t')
    else:
        print('Could not find appropriate column to fill with hashes')


def contains(small_list, big_list):
    """Checks if a list is contained in another list

    :param small_list: The smaller list
    :param big_list: The bigger list in which to find the smaller list
    :return: True if the small list is found inside the big list
    """
    if len(small_list) == 0:
        return False

    for i in iter(range(len(big_list) - len(small_list) + 1)):
        for j in iter(range(len(small_list))):
            if big_list[i + j] != small_list[j]:
                break
        else:
            return i, i + len(small_list)
    return False


def create_isatab_archive(inv_fp, target_filename=None,
                          filter_by_measurement=None):
    """Function to create an ISArchive; option to select by assay
    measurement type

    :param inv_fp: A file-like buffer object pointing to an investigation file
    :param target_filename: Target ZIP file name
    :param filter_by_measurement: Select by measurement type
    :return: List of files zipped if successful, None if not successful
    """
    if target_filename is None:
        target_filename = os.path.join(
            os.path.dirname(inv_fp.name), 'isatab.zip')
    ISA = isatab.load(inv_fp)

    all_files_in_isatab = []
    found_files = []

    for s in ISA.studies:
        if filter_by_measurement is not None:
            log.debug('Selecting ', filter_by_measurement)
            selected_assays = [
                a for a in s.assays if
                a.measurement_type.term == filter_by_measurement]
        else:
            selected_assays = s.assays

        for a in selected_assays:
            all_files_in_isatab += [d.filename for d in a.data_files]
    dirname = os.path.dirname(inv_fp.name)

    for fname in all_files_in_isatab:
        if os.path.isfile(os.path.join(dirname, fname)):
            found_files.append(fname)
    missing_files = [f for f in all_files_in_isatab if f not in found_files]

    if len(missing_files) == 0:
        log.debug('Do zip')
        with ZipFile(target_filename, mode='w') as zip_file:
            # use relative dir_name to avoid absolute path on file names
            zip_file.write(inv_fp.name, arcname=os.path.basename(inv_fp.name))

            for s in ISA.studies:
                zip_file.write(
                    os.path.join(dirname, s.filename), arcname=s.filename)

                for a in selected_assays:
                    zip_file.write(
                        os.path.join(dirname, a.filename), arcname=a.filename)

            for file in all_files_in_isatab:
                zip_file.write(os.path.join(dirname, file), arcname=file)

            log.debug(zip_file.namelist())
            return zip_file.namelist()

    else:
        log.debug('Not zipping')
        log.debug('Missing: ', missing_files)
        return None


def squashstr(string):
    """Squashes a string by removing the spaces and lowering it"""

    nospaces = "".join(string.split())
    return nospaces.lower()


def pyvar(string):
    """Transform a string into a valid Python variable

    :param string: Input string
    :return: Python variable-compatible string
    """
    for ch in string:
        if ch.isalpha() or ch.isdigit():
            pass
        else:
            string = string.replace(ch, '_')
    return string


def recast_columns(columns):
    """Cast columns to Characteristics and Parameter Values

    :param columns: String list of columns to cast
    :return: The casted columns as a list
    """
    casting_map = {
        'Material Type': 'Characteristics[Material Type]',
        'Date': 'Parameter Value[Date]',
        'Performer': 'Parameter Value[Performer]'
    }
    for k, v in casting_map.items():
        columns = list(map(lambda x: v if x == k else x, columns))
    return columns


def pyisatabify(dataframe):
    """Processes a DataFrame into a dict

    :param dataframe: Input DataFrame
    :return: A dict of columns
    """
    columns = dataframe.columns

    pycolumns = []
    col2pymap = {}

    nodecontext = None
    attrcontext = None

    columns = recast_columns(columns=columns)

    for column in columns:
        squashedcol = squashstr(column)

        if squashedcol.endswith(('name', 'file')) or \
                squashedcol == 'protocolref':
            nodecontext = squashedcol
            pycolumns.append(squashedcol)
        elif squashedcol.startswith(
                ('characteristics', 'parametervalue', 'comment',
                 'factorvalue')) and nodecontext is not None:
            attrcontext = squashedcol

            # factor values are not in node context
            if attrcontext == 'factorvalue':
                pycolumns.append(pyvar(attrcontext))
            else:
                pycolumns.append(
                    '{0}__{1}'.format(nodecontext, pyvar(attrcontext)))

        elif squashedcol.startswith(('term', 'unit')) and \
                nodecontext is not None and attrcontext is not None:
            pycolumns.append('{0}__{1}_{2}'.format(nodecontext,
                                                   pyvar(attrcontext),
                                                   pyvar(squashedcol)))

        col2pymap[column] = pycolumns[-1]
    return col2pymap


def factor_query_isatab(df, q):
    """Query a ISA-Tab DataFrame using the Pandas query functions

    :param df: A Pandas DataFrame
    :param q: Query, like "rate is 0.2 and limiting nutrient is sulphur"
    :return: DataFrame sliced on the query
    """
    columns = df.columns
    columns = recast_columns(columns=columns)

    for i, column in enumerate(columns):
        columns[i] = pyvar(column) if \
            column.startswith('Factor Value[') else column

    df.columns = columns

    qlist = q.split(' and ')
    fmt_query = []

    for factor_query in qlist:
        factor_value = factor_query.split(' == ')

        fmt_query_part = "Factor_Value_{0}_ == '{1}'".format(
            pyvar(factor_value[0]), factor_value[1])
        fmt_query.append(fmt_query_part)

    fmt_query = ' and '.join(fmt_query)
    log.debug('running query: {}'.format(fmt_query))
    return df.query(fmt_query)


def check_loadable(tab_dir_root):
    """Checks if each study in a directory full of MetaboLights ISA-Tabs can
    be loaded

    :param tab_dir_root: Root of the MTBLS directories
    :return: None
    """
    for mtbls_dir in [x for x in os.listdir(tab_dir_root) if
                      x.startswith('MTBLS')]:
        try:
            isatab.load(os.path.join(tab_dir_root, mtbls_dir))
            print('{} load OK'.format(mtbls_dir))
        except Exception as e:
            print('{0} load FAIL, reason: {1}'.format(mtbls_dir, e))


def compute_study_factors_on_mtbls(tab_dir_root):
    """Produces study factors report

    :param tab_dir_root: Directory containing MTBLS prefixed ISA-Tab
    directories
    :return: None, output writes to stdout

    Usage:
        >>> compute_study_factors_on_mtbls('tests/data')
        # if tests/data contains tests/data/MTBLS1, tests/data/MTBLS2 etc.

        To write to an output file:
        >>> import sys
        >>> stdout_console = sys.stdout  # save normal stdout
        >>> sys.stdout = open('out.txt')  # set to file
        >>> compute_study_factors_on_mtbls('tests/data')
        >>> sys.stdout = stdout_console  # reset stdout
    """
    for mtbls_dir in [x for x in os.listdir(tab_dir_root)
                      if x.startswith('MTBLS')]:
        study_dir = os.path.join(tab_dir_root, mtbls_dir)
        analyzer = IsaTabAnalyzer(study_dir)
        try:
            analyzer.pprint_study_design_report()
        except ValueError:
            pass


class IsaTabAnalyzer(object):
    """A utility to analyze ISA-Tabs"""

    def __init__(self, path):
        self.path = path

    def generate_study_design_report(self, get_num_study_groups=True,
                                     get_factors=True, get_num_levels=True,
                                     get_levels=True, get_study_groups=True):
        """Generates a study design report
        :return: JSON report
        """
        isa = isatab.load(self.path, skip_load_tables=False)
        study_design_report = []
        raw_data_file_prefix = ('Raw', 'Array', 'Free Induction Decay')
        for study in isa.studies:
            study_key = study.identifier if study.identifier != '' \
                else study.filename
            study_design_report.append({
                'study_key': study_key,
                'total_sources': len(study.sources),
                'total_samples': len(study.samples),
                'assays': []
            })
            with open(os.path.join(self.path, study.filename)) as s_fp:
                s_df = isatab.load_table(s_fp)
                for assay in study.assays:
                    assay_key = '/'.join([assay.filename,
                                          assay.measurement_type.term,
                                          assay.technology_type.term,
                                          assay.technology_platform])
                    assay_report = {
                        'assay_key': assay_key,
                        'num_sources': len(assay.samples),
                        'num_samples': len([x for x in assay.data_files
                                            if x.label.startswith(
                                                raw_data_file_prefix)])
                    }
                    with open(os.path.join(self.path, assay.filename)) as a_fp:
                        a_df = isatab.load_table(a_fp)
                        merged_df = pd.merge(s_df, a_df, on='Sample Name')
                        factor_cols = [x for x in merged_df.columns if
                                       x.startswith("Factor Value")]
                        if len(factor_cols) > 0:
                            # add branch to get all if no FVs
                            study_group_factors_df = \
                                merged_df[factor_cols].drop_duplicates()
                            factors_list = [x[13:-1] for x in
                                            study_group_factors_df.columns]
                            queries = []
                            factors_and_levels = {}
                            for i, row in study_group_factors_df.iterrows():
                                fvs = []
                                for x, y in zip(factors_list, row):
                                    fvs.append(' == '.join([x, str(y)]))
                                    try:
                                        factor_and_levels = \
                                            factors_and_levels[x]
                                    except KeyError:
                                        factors_and_levels[x] = set()
                                        factor_and_levels = \
                                            factors_and_levels[x]
                                    factor_and_levels.add(str(y))
                                queries.append(' and '.join(fvs))
                            assay_report['total_study_groups'] = len(queries)
                            assay_report['factors_and_levels'] = []
                            assay_report['group_summary'] = []
                            for k, v in factors_and_levels.items():
                                assay_report['factors_and_levels'].append({
                                    'factor': k,
                                    'num_levels': len(v),
                                })
                            for query in queries:
                                try:
                                    columns = merged_df.columns
                                    columns = recast_columns(columns=columns)
                                    for i, column in enumerate(columns):
                                        columns[i] = pyvar(column) if \
                                            column.startswith(
                                                'Factor Value[') else column
                                    merged_df.columns = columns
                                    qlist = query.split(' and ')
                                    fmt_query = []
                                    for factor_query in qlist:
                                        factor_value = \
                                            factor_query.split(' == ')
                                        fmt_query_part = \
                                            "Factor_Value_{0}_ == '{1}'"\
                                            .format(pyvar(factor_value[0]),
                                                    factor_value[1])
                                        fmt_query.append(fmt_query_part)
                                    fmt_query = ' and '.join(fmt_query)
                                    log.debug('running query: {}'.format(
                                        fmt_query))
                                    df2 = merged_df.query(fmt_query)
                                    data_column = [x for x in merged_df.columns
                                                   if x.startswith(
                                                       raw_data_file_prefix)
                                                   and x.endswith(
                                                       'Data File')][0]
                                    assay_report['group_summary'].append(
                                        dict(study_group=query,
                                             sources=len(
                                                 list(df2['Source Name']
                                                      .drop_duplicates())),
                                             samples=len(
                                                 list(df2['Sample Name']
                                                      .drop_duplicates())),
                                             raw_files=len(
                                                 list(df2[data_column]
                                                      .drop_duplicates()))
                                             ))
                                except Exception as e:
                                    print("error in query, {}".format(e))
                    study_design_report[-1]['assays'].append(assay_report)
        return study_design_report

    def pprint_study_design_report(self):
        """Pretty prints the study design report"""
        print(json.dumps(self.generate_study_design_report(), indent=4,
                         sort_keys=True))

    def compute_stats(self):
        """Computes some statistics about the ISA-Tab study"""
        isa = isatab.load(self.path, skip_load_tables=False)
        print('-------------------------------------------')
        print('Investigation stats')
        print('-------------------------------------------')
        print('Num ontologies declared: {}'.format(
            len(isa.ontology_source_references)))
        print('Num studies: {}'.format(len(isa.studies)))
        print('Total study assays: {}'.format(
            reduce(lambda x, y: len(x.assays) + len(y.assays), isa.studies)))
        print('Num investigation publications: {}'.format(
            len(isa.publications)))
        print('Total study publications: {}'.format(
            reduce(lambda x, y: len(x.publications) + len(y.publications),
                   isa.studies)))
        print('Num study people: {}'.format(len(isa.contacts)))
        print('Total study people: {}'.format(
            reduce(lambda x, y: len(x.contacts) + len(y.contacts),
                   isa.studies)))
        for study in isa.studies:
            print('-------------------------------------------')
            print('Study stats for {}'.format(study.filename))
            print('-------------------------------------------')
            print('Num assays: {}'.format(len(study.assays)))
            from collections import Counter
            counter = Counter()
            for material in study.sources + study.samples + \
                    study.other_material:
                counter.update(material.characteristics)
            for k, v in counter.items():
                print('{characteristic} used {num} times'.format(
                    characteristic=k, num=v))


def batch_fix_isatabs(settings):
    """Batch fix some ISA-Tabs based on some predefined settings

    :param settings: JSON describing what to fix

    settings = {
        "/path/to/MTBLS1/s_MTBLS1.txt": [
            {
                "factor": "Metabolic syndrome",
                "protocol_ref": None
            },
            {
                "factor": "Gender",
                "protocol_ref": "Sample collection"
            }
        ],
        "/path/to/MTBLS2/s_MTBL2.txt": [
            {
                "factor": "genotype",
                "protocol_ref": None
            }
        ]
    }

    :return: None
    """
    for table_file_path in settings.keys():
        print('Fixing {table_file_path}...'.format(
            table_file_path=table_file_path))
        fixer = IsaTabFixer(table_file_path=table_file_path)
        fixer.fix_factor(
            factor_name=settings[table_file_path]['factor'],
            protocol_ref=settings[table_file_path]['protocol_ref'])


class IsaTabFixer(object):
    """Utilities to fix ISA-Tabs"""

    def __init__(self, table_file_path):
        self.path = table_file_path

    @staticmethod
    def clean_isatab_field_names(field_names):
        """Fixes ISA-Tab header names. Sometimes Pandas adds suffixes to them
        uneccessarily or other junk

        :param field_names: List of field header names
        :return: Fixed field names
        """
        # iterates field names and drops the postfix enums that pandas adds
        for i, field_name in enumerate(field_names):
            if field_name.startswith('Term Source REF'):
                field_names[i] = 'Term Source REF'
            elif field_name.startswith('Term Accession Number'):
                field_names[i] = 'Term Accession Number'
            elif field_name.startswith('Unit'):
                field_names[i] = 'Unit'
            elif 'Characteristics[' in field_name:
                if 'material type' in field_name.lower():
                    field_names[i] = 'Material Type'
                else:
                    try:
                        field_names[i] = field_name[field_name.rindex(
                            '.') + 1:]
                    except ValueError:
                        pass
            elif 'Factor Value[' in field_name:
                try:
                    field_names[i] = field_name[field_name.rindex('.') + 1:]
                except ValueError:
                    pass
            elif 'Parameter Value[' in field_name:
                try:
                    field_names[i] = field_name[field_name.rindex('.') + 1:]
                except ValueError:
                    pass
            elif field_name.endswith('Date'):
                field_names[i] = 'Date'
            elif field_name.endswith('Performer'):
                field_names[i] = 'Performer'
            elif 'Protocol REF' in field_name:
                field_names[i] = 'Protocol REF'
            elif field_name.startswith('Sample Name.'):
                field_names[i] = 'Sample Name'
        return field_names

    def fix_factor(self, factor_name, protocol_ref=None):
        """Fixes a factor if it's supposed to be something else

        :param factor_name: The factor that's incorrect
        :param protocol_ref: The protocol REF to add the Parameter Value
        corresponding to the wrong factor as above
        :return: None
        """
        if protocol_ref is None:
            self.replace_factor_with_source_characteristic(
                factor_name=factor_name)
        else:
            self.replace_factor_with_protocol_parameter_value(
                factor_name=factor_name, protocol_ref=protocol_ref)

    def replace_factor_with_source_characteristic(self, factor_name):
        """Fixes a factor if it's supposed to be a source characteristic
        attached

        :param factor_name: The factor that's incorrect
        :return: None
        """
        table_file_df = isatab.read_tfile(self.path)

        field_names = list(table_file_df.columns)
        clean_field_names = self.clean_isatab_field_names(field_names)

        factor_index = clean_field_names.index(
            'Factor Value[{}]'.format(factor_name))
        source_name_index = clean_field_names.index('Source Name')

        if factor_index < len(field_names) and \
            'Term Source REF' in field_names[factor_index + 1] and \
                'Term Accession' in field_names[factor_index + 2]:
            log.debug(
                'Moving Factor Value[{}] with term columns'.format(
                    factor_name))
            # move Factor Value and Term Source REF and Term Accession columns
            field_names.insert(
                source_name_index + 1,
                field_names[factor_index])
            field_names.insert(
                source_name_index + 2, field_names[factor_index + 1 + 1])
            field_names.insert(
                source_name_index + 3, field_names[factor_index + 2 + 2])

            del field_names[factor_index + 3]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 2]  # del Term Source REF
            del field_names[factor_index + 2 + 1]  # del Term Accession
        elif factor_index < len(field_names) and \
            'Unit' in field_names[factor_index + 1] and \
                'Term Source REF' in field_names[factor_index + 2] and \
                'Term Accession' in field_names[factor_index + 3]:
            log.debug(
                'Moving Factor Value[{}] with unit term columns'.format(
                    factor_name))
            # move Factor Value and Unit as ontology annotation
            field_names.insert(
                source_name_index + 1,
                field_names[factor_index])
            field_names.insert(
                source_name_index + 2, field_names[factor_index + 1 + 1])
            field_names.insert(
                source_name_index + 3, field_names[factor_index + 2 + 2])
            field_names.insert(
                source_name_index + 4, field_names[factor_index + 3 + 3])

            del field_names[factor_index + 4]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 3]  # del Unit
            del field_names[factor_index + 2 + 2]  # del Term Source REF
            del field_names[factor_index + 3 + 1]  # del Term Accession
        elif factor_index < len(field_names) and \
                'Unit' in field_names[factor_index + 1]:
            log.debug(
                'Moving Factor Value[{}] with unit column'.format(factor_name))
            # move Factor Value and Unit columns
            field_names.insert(
                source_name_index + 1,
                field_names[factor_index])
            field_names.insert(
                source_name_index + 2, field_names[factor_index + 1 + 1])

            del field_names[factor_index + 2]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 1]  # del Unit
        else:  # move only the Factor Value column
            log.debug('Moving Factor Value[{}]'.format(factor_name))
            field_names.insert(
                source_name_index + 1,
                field_names[factor_index])
            del field_names[factor_index]  # del Factor Value[{}]

        table_file_df.columns = self.clean_isatab_field_names(field_names)

        # Rename Factor Value column to Characteristics column
        field_names_modified = list(table_file_df.columns)
        field_names_modified[source_name_index + 1] = \
            field_names_modified[source_name_index + 1].replace(
                'Factor Value', 'Characteristics')
        table_file_df.columns = self.clean_isatab_field_names(
            field_names_modified)

        with open(self.path, 'w') as out_fp:
            table_file_df.to_csv(path_or_buf=out_fp, index=False, sep='\t',
                                 encoding='utf-8')

    def replace_factor_with_protocol_parameter_value(
            self, factor_name, protocol_ref):
        """Fixes a factor if it's supposed to be a Parameter Value

        :param factor_name: The factor that's incorrect
        :param protocol_ref: Protocol REF for the new Parameter Value
        :return: None
        """
        table_file_df = isatab.read_tfile(self.path)

        field_names = list(table_file_df.columns)
        clean_field_names = self.clean_isatab_field_names(field_names)

        factor_index = clean_field_names.index(
            'Factor Value[{factor_name}]'.format(factor_name=factor_name))

        with open(self.path) as tfile_fp:
            next(tfile_fp)
            line1 = next(tfile_fp)
            protocol_ref_index = list(
                map(lambda x: x[1:-1] if x[0] == '"' and x[-1] == '"' else x,
                    line1.split('\t'))).index(protocol_ref)

        if protocol_ref_index < 0:
            raise IOError(
                'Could not find protocol ref matching {protocol_ref}'
                .format(protocol_ref=protocol_ref))

        if factor_index < len(field_names) and \
            'Term Source REF' in field_names[factor_index + 1] and \
                'Term Accession' in field_names[factor_index + 2]:
            log.debug(
                'Moving Factor Value[{}] with term columns'.format(
                    factor_name))
            # move Factor Value and Term Source REF and Term Accession columns
            field_names.insert(
                protocol_ref_index + 1, field_names[factor_index])
            field_names.insert(
                protocol_ref_index + 2, field_names[factor_index + 1 + 1])
            field_names.insert(
                protocol_ref_index + 3, field_names[factor_index + 2 + 2])
            del field_names[factor_index + 3]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 2]  # del Term Source REF
            del field_names[factor_index + 2 + 1]  # del Term Accession
        elif factor_index < len(field_names) and \
            'Unit' in field_names[factor_index + 1] and \
                'Term Source REF' in field_names[factor_index + 2] and \
                'Term Accession' in field_names[factor_index + 3]:
            log.debug(
                'Moving Factor Value[{factor_name}] with unit term columns'
                .format(factor_name=factor_name))
            # move Factor Value and Unit as ontology annotation
            field_names.insert(
                protocol_ref_index + 1, field_names[factor_index])
            field_names.insert(
                protocol_ref_index + 2, field_names[factor_index + 1 + 1])
            field_names.insert(
                protocol_ref_index + 3, field_names[factor_index + 2 + 2])
            field_names.insert(
                protocol_ref_index + 4, field_names[factor_index + 3 + 3])
            del field_names[factor_index + 4]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 3]  # del Unit
            del field_names[factor_index + 2 + 2]  # del Term Source REF
            del field_names[factor_index + 3 + 1]  # del Term Accession
        elif factor_index < len(field_names) and \
                'Unit' in field_names[factor_index + 1]:
            log.debug(
                'Moving Factor Value[{factor_name}] with unit column'
                .format(factor_name=factor_name))
            # move Factor Value and Unit columns
            field_names.insert(
                protocol_ref_index + 1, field_names[factor_index])
            field_names.insert(
                protocol_ref_index + 2, field_names[factor_index + 1 + 1])
            del field_names[factor_index + 2]  # del Factor Value[{}]
            del field_names[factor_index + 1 + 1]  # del Unit
        else:  # move only the Factor Value column
            log.debug('Moving Factor Value[{factor_name}]'
                      .format(factor_name=factor_name))
            field_names.insert(
                protocol_ref_index + 1, field_names[factor_index])
            del field_names[factor_index]  # del Factor Value[{}]

        table_file_df.columns = self.clean_isatab_field_names(field_names)

        # Rename Factor Value column to Parameter Value column
        field_names_modified = list(table_file_df.columns)
        field_names_modified[protocol_ref_index + 1] = \
            field_names_modified[protocol_ref_index + 1].replace(
                'Factor Value', 'Parameter Value')
        table_file_df.columns = self.clean_isatab_field_names(
            field_names_modified)

        investigation = isatab.load(
            os.path.dirname(self.path), skip_load_tables=True)
        study = investigation.studies[-1]
        protocol = study.get_prot(protocol_ref)
        if protocol is None:
            raise ISAModelAttributeError(
                'No protocol with name {protocol_ref} was found'.format(
                    protocol_ref=protocol_ref))
        protocol.add_param(factor_name)
        factor = study.get_factor(factor_name)
        if factor is None:
            raise ISAModelAttributeError(
                'No factor with name {factor_name} was found'.format(
                    factor_name=factor_name))
        else:
            study.del_factor(name=factor_name, are_you_sure=True)

        study.filename = '{study_filename}.fix'.format(
            study_filename=study.filename)

        isatab.dump(
            investigation, output_path=os.path.dirname(self.path),
            i_file_name='i_Investigation.txt.fix', skip_dump_tables=True)

        with open(os.path.join(
                os.path.dirname(self.path), '{s_filename}.fix'.format(
                    s_filename=os.path.basename(self.path))), 'w') as out_fp:
            table_file_df.to_csv(path_or_buf=out_fp, index=False, sep='\t',
                                 encoding='utf-8')

    def remove_unused_protocols(self):
        """Removes usused protocols

        :return: None
        """
        investigation = isatab.load(os.path.dirname(self.path))
        for study in investigation.studies:
            unused_protocol_names = set(x.name for x in study.protocols)
            for process in study.process_sequence:
                try:
                    unused_protocol_names.remove(
                        process.executes_protocol.name)
                except KeyError:
                    pass
            for assay in study.assays:
                for process in assay.process_sequence:
                    try:
                        unused_protocol_names.remove(
                            process.executes_protocol.name)
                    except KeyError:
                        pass
            print('Unused protocols: {}'.format(unused_protocol_names))
            # remove these protocols from study.protocols
            clean_protocols_list = []
            for protocol in study.protocols:
                if protocol.name not in unused_protocol_names:
                    clean_protocols_list.append(protocol)
            study.protocols = clean_protocols_list
        isatab.dump(
            investigation, output_path=os.path.dirname(self.path),
            i_file_name='{filename}.fix'.format(
                filename=os.path.basename(self.path)), skip_dump_tables=True)


def utf8_text_file_open(path):
    """A function to correctly open utf-8 files in Python 2 and Python 3

    :param path: Path to a file
    :return: A file-like buffer object opened with utf-8 encoding
    """
    if sys.version_info[0] < 3:
        fp = open(path, 'rb')
    else:
        fp = open(path, 'r', newline='', encoding='utf8')
    return fp


def create_and_merge_mzml(
        galaxy_prameters_file, mapping_file, data_dir, output_dir):
    """Runs the create mode and merges mzML input  files for the CUDDEL
    merger

    :param galaxy_prameters_file: Galaxy inputs JSON
    :param mapping_file: a mapping JSON
    :param data_dir: Input data directory containing the mzMLs
    :param output_dir: output for the merged outputs
    :return: None
    """
    tmp = tempfile.mkdtemp()
    create_from_galaxy_parameters(
        galaxy_parameters_file=galaxy_prameters_file, target_dir=tmp)
    ISA = isatab.load(tmp)
    shutil.rmtree(tmp)

    # load mzml metadata from files in data_dir
    mzml_meta = {}
    for i, mzml in enumerate(os.listdir(data_dir)):
        if not mzml.lower().endswith('.mzml'):
            continue
        mzml_pth = os.path.join(data_dir, mzml)
        # mz = mzMLmeta(mzml_pth)
        mz= mzml.MzMLFile(mzml_pth)
        # mz = mzml_meta(mzml_pth)

        mzml_name, _ = os.path.splitext(mzml_pth)
        mzml_meta[os.path.basename(mzml_name)] = mz.meta

    # only get first assay from first study obj
    study = ISA.studies[0]

    mapping = {}
    with open(mapping_file) as fp:
        mapping = json.load(fp)

    for assay in study.assays:
        # get mass spectrometry processes only
        ms_processes = [
            x for x in assay.process_sequence if
            x.executes_protocol.protocol_type.term == 'mass spectrometry']
        # insert the new parameter values
        for k, v in mapping.items():
            with open(os.path.join('MTBLS265-no-binary', 'json_meta',
                                   v + '.json')) as fp2:
                mzml_meta = json.load(fp2)
                data_trans_meta = {k: v for k, v in mzml_meta.items() if
                                   k.startswith('Data Transformation')}
                labels = {k: v for k, v in mzml_meta.items() if
                          k.endswith(('File', 'Name'))}
                ms_prot_meta = {k: v for k, v in mzml_meta.items() if
                                not k.startswith('Data Transformation') and
                                k not in labels.keys()}
            try:  # add parameter values to MS process
                ms_process = [x for x in ms_processes if
                              k in [y.filename for y in x.outputs]][0]
                # pvs = ms_process.parameter_values
                for item in ms_prot_meta:
                    if not ms_process.executes_protocol.get_param(item):
                        ms_process.executes_protocol.add_param(item)
                    param = ms_process.executes_protocol.get_param(item)
                    meta_item = ms_prot_meta[item]
                    if 'value' in meta_item.keys():
                        value = meta_item['value']  # check for unit as well
                    elif 'name' in meta_item.keys():
                        value = meta_item[
                            'name']  # check for ontology annotation
                    elif 'entry_list' in meta_item.keys():
                        values = meta_item['entry_list']
                        if 'value' in values[-1].keys():
                            value = values[-1][
                                'value']  # check for unit as well
                        elif 'name' in values[-1].keys():
                            value = values[-1][
                                'name']  # check for ontology annotation
                        else:
                            raise IOError(values[-1])
                    else:
                        raise IOError(meta_item)
                    pv = ParameterValue(category=param, value=value)
                    ms_process.parameter_values.append(pv)

                # set raw file name to mzML meta raw file name and sample name
                # too
                for output in ms_process.outputs:
                    if output.label in labels.keys():
                        output.filename = labels[
                            output.label]['entry_list'][-1]['value']
                        output.generated_from[-1].name = labels[
                            'Sample Name']['value']
                #  set MS Assay Name to mzML metadata
                ms_process.name = labels['MS Assay Name']['value']

                #  add data transformation to describe conversion to mzML
                if data_trans_meta['Data Transformation Name']:
                    if not study.get_prot('Conversion to mzML'):
                        dt_prot = Protocol(name='Conversion to mzML',
                                           protocol_type=OntologyAnnotation(
                                               term='data transformation'))
                        dt_prot.add_param('peak picking')
                        dt_prot.add_param('software')
                        dt_prot.add_param('software version')
                        study.protocols.append(dt_prot)
                    dt_prot = study.get_prot('Conversion to mzML')
                    dt_process = Process(executes_protocol=dt_prot)
                    dt_process.outputs = [DerivedSpectralDataFile(
                        filename=labels['Derived Spectral Data File'][
                            'entry_list'][-1]['value'])]
                    dt_process.inputs = ms_process.outputs
                    plink(ms_process, dt_process)
                    assay.process_sequence.append(dt_process)
            except IndexError:
                pass

    isatab.dump(ISA, output_dir)
