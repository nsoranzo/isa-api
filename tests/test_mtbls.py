from __future__ import absolute_import
import unittest
from unittest.mock import patch, mock_open
from isatools.net import mtbls as MTBLS
import shutil
import os

import unittest
import os
import pandas as pd
import shutil
import tempfile
from io import StringIO

from isatools import isatab
from isatools.io import isatab_parser
from isatools.isatab import ProcessSequenceFactory
from isatools.model import *
from isatools.tests.utils import assert_tab_content_equal
from isatools.tests import utils
from isatools.isatab import IsaTabDataFrame

class TestMtblsIO(unittest.TestCase):

    def setUp(self):
        # pass  # detect if MTBLS is reachable. If so, run test of real server, otherwise run Mocks only?
        self._tab_data_dir = utils.TAB_DATA_DIR
        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        pass

    """Mock-only test on MTBLS1"""
    @patch('ftplib.FTP', autospec=True)
    def test_get_study(self, mock_ftp_constructor):
        mock_ftp = mock_ftp_constructor.return_value
        mock_ftp.login.return_value = '230'  # means login OK
        tmp_dir = MTBLS.get('MTBLS1')  # only retrieves ISA files from MTBLS
        self.assertTrue(mock_ftp.login.called)
        mock_ftp_constructor.assert_called_with('ftp.ebi.ac.uk')
        mock_ftp.cwd.assert_called_with('/pub/databases/metabolights/studies/public/MTBLS1')
        shutil.rmtree(tmp_dir)

    """Tries to do actual call on MetaboLights; uses MTBLS2 as not so big"""
    # def test_get_study_as_tab(self):
    #     tmp_dir = MTBLS.getj('MTBLS2')  # gets MTBLS ISA-Tab files
    #     self.assertEqual(len(os.listdir(tmp_dir)), 3)
    #     self.assertSetEqual(set(os.listdir(tmp_dir)), {'a_mtbl2_metabolite profiling_mass spectrometry.txt',
    #                                                'i_Investigation.txt', 's_MTBL2.txt'})
    #     shutil.rmtree(tmp_dir)

    # def test_get_study_as_json(self):
    #     isa_json = MTBLS.get('MTBLS2')  # loads MTBLS study into ISA JSON
    #     self.assertIsInstance(isa_json, dict)
    #     self.assertEqual(isa_json['identifier'], 'MTBLS2')
    #     self.assertEqual(isa_json['studies'][0]['people'][0]['email'], 'boettch@ipb-halle.de')

    # def test_get_factor_names(self):
    #     factors = MTBLS.get_factor_names('MTBLS2')
    #     self.assertIsInstance(factors, set)
    #     self.assertEqual(len(factors), 2)
    #     self.assertSetEqual(factors, {'genotype', 'replicate'})

    # def test_get_factor_values(self):
    #     fvs = MTBLS.get_factor_values('MTBLS2', 'genotype')
    #     self.assertIsInstance(fvs, set)
    #     self.assertEqual(len(fvs), 2)
    #     self.assertSetEqual(fvs, {'Col-0', 'cyp79'})

    # def test_get_datafiles(self):
    #     datafiles = MTBLS.get_data_files('MTBLS2')
    #     self.assertIsInstance(datafiles, list)
    #     self.assertEqual(len(datafiles), 16)
    #     factor_selection = {"genotype": "Col-0"}
    #     results = MTBLS.get_data_files('MTBLS2', factor_selection)
    #     self.assertEqual(len(results), 8)
    #     self.assertEqual(len(results[0]['data_files']), 1)

    def test_get_datafiles_multiple_factors(self):
        factor_selection = {"Gender": "Male",
                            "Metabolic syndrome": "Control Group"}
        results = MTBLS.get_data_files('MTBLS1', factor_selection)
        self.assertEqual(len(results), 56)
        self.assertEqual(len(results[0]['data_files']), 1)
        self.assertLess(
            len(
                MTBLS.get_data_files('MTBLS1', {
                    "Gender": "Male",
                    "Metabolic syndrome": "Control Group"
                })
            ),
            len(
                MTBLS.get_data_files('MTBLS1', {
                    "Gender": "Male"
                })
            )
        )

    def test_get_factors_summary(self):  # Test for issue #221
        factors_summary = MTBLS.get_factors_summary('MTBLS26')
        self.assertIsInstance(factors_summary, list)
        self.assertEqual(len(factors_summary), 18)

    def test_get_data_for_sample(self):
        hits = MTBLS.get_data_for_sample(
            'MTBLS108', sample_name='Lut_C_223h')
        self.assertEqual(len(hits), 2)
        self.assertIn(
            'm_study_p_c_metabolite_profiling_mass_spectrometry_v2_maf.tsv',
            [x.filename for x in hits])
        self.assertIn('Lut_C_223h.raw', [x.filename for x in hits])