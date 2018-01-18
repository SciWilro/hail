from __future__ import print_function  # Python 2 and 3 print compatibility

import unittest

from hail2 import *
from subprocess import call as syscall
import numpy as np
from struct import unpack
import hail.utils as utils

hc = None

def setUpModule():
    global hc
    hc = HailContext()  # master = 'local[2]')


def tearDownModule():
    global hc
    hc.stop()
    hc = None


class Tests(unittest.TestCase):
    _dataset = None

    def get_dataset(self):
        if Tests._dataset is None:
            Tests._dataset = hc.import_vcf('src/test/resources/sample.vcf').to_hail1().split_multi_hts().to_hail2()
        return Tests._dataset

    @property
    def test_resources(self):
        return "src/test/resources"

    def test_ibd(self):
        dataset = self.get_dataset()

        def plinkify(ds, min=None, max=None):
            vcf = utils.new_temp_file(prefix="plink", suffix="vcf")
            plinkpath = utils.new_temp_file(prefix="plink")
            ds.to_hail1().export_vcf(vcf)
            threshold_string = "{} {}".format("--min {}".format(min) if min else "",
                                              "--max {}".format(max) if max else "")

            plink_command = "plink --double-id --allow-extra-chr --vcf {} --genome full --out {} {}"\
                .format(utils.get_URI(vcf),
                        utils.get_URI(plinkpath),
                        threshold_string)
            result_file = utils.get_URI(plinkpath + ".genome")

            syscall(plink_command, shell=True)

            ### format of .genome file is:
            # _, fid1, iid1, fid2, iid2, rt, ez, z0, z1, z2, pihat, phe,
            # dst, ppc, ratio, ibs0, ibs1, ibs2, homhom, hethet (+ separated)

            ### format of ibd is:
            # i (iid1), j (iid2), ibd: {Z0, Z1, Z2, PI_HAT}, ibs0, ibs1, ibs2
            results = {}
            with open(result_file) as f:
                f.readline()
                for line in f:
                    row = line.strip().split()
                    results[(row[1], row[3])] = (map(float, row[6:10]),
                                                 map(int, row[14:17]))
            return results

        def compare(ds, min=None, max=None):
            plink_results = plinkify(ds, min, max)
            hail_results = methods.ibd(ds, min=min, max=max).collect()

            for row in hail_results:
                key = (row.i, row.j)
                self.assertAlmostEqual(plink_results[key][0][0], row.ibd.Z0, places=4)
                self.assertAlmostEqual(plink_results[key][0][1], row.ibd.Z1, places=4)
                self.assertAlmostEqual(plink_results[key][0][2], row.ibd.Z2, places=4)
                self.assertAlmostEqual(plink_results[key][0][3], row.ibd.PI_HAT, places=4)
                self.assertEqual(plink_results[key][1][0], row.ibs0)
                self.assertEqual(plink_results[key][1][1], row.ibs1)
                self.assertEqual(plink_results[key][1][2], row.ibs2)

        compare(dataset)
        compare(dataset, min=0.0, max=1.0)
        dataset = dataset.annotate_rows(dummy_maf=0.01)
        methods.ibd(dataset, dataset['dummy_maf'], min=0.0, max=1.0)
        methods.ibd(dataset, dataset['dummy_maf'].to_float32(), min=0.0, max=1.0)

    def test_ld_matrix(self):
        dataset = self.get_dataset()

        ldm = methods.ld_matrix(dataset, force_local=True)

    def test_linreg(self):
        dataset = hc.import_vcf('src/test/resources/regressionLinear.vcf')

        phenos = hc.import_table('src/test/resources/regressionLinear.pheno',
                                 types={'Pheno': TFloat64()},
                                 key='Sample')
        covs = hc.import_table('src/test/resources/regressionLinear.cov',
                               types={'Cov1': TFloat64(), 'Cov2': TFloat64()},
                               key='Sample')

        dataset = dataset.annotate_cols(pheno=phenos[dataset.s], cov = covs[dataset.s])
        dataset = methods.linreg(dataset,
                         ys=dataset.pheno,
                         x=dataset.GT.num_alt_alleles(),
                         covariates=[dataset.cov.Cov1, dataset.cov.Cov2 + 1 - 1])

        dataset.count_rows()

    def test_trio_matrix(self):
        ped = Pedigree.read('src/test/resources/triomatrix.fam')
        from hail import KeyTable
        fam_table = KeyTable.import_fam('src/test/resources/triomatrix.fam').to_hail2()

        dataset = hc.import_vcf('src/test/resources/triomatrix.vcf')
        dataset = dataset.annotate_cols(fam = fam_table[dataset.s])

        tm = methods.trio_matrix(dataset, ped, complete_trios=True)

        tm.count_rows()

    def test_sample_qc(self):
        dataset = self.get_dataset()
        dataset = methods.sample_qc(dataset)

    def test_grm(self):
        tolerance = 0.001

        def load_id_file(path):
            ids = []
            with hadoop_read(path) as f:
                for l in f:
                    r = l.strip().split('\t')
                    self.assertEqual(len(r), 2)
                    ids.append(r[1])
            return ids

        def load_rel(ns, path):
            rel = np.zeros((ns, ns))
            with hadoop_read(path) as f:
                for i,l in enumerate(f):
                    for j,n in enumerate(map(float, l.strip().split('\t'))):
                        rel[i,j] = n
                    self.assertEqual(j, i)
                self.assertEqual(i, ns - 1)
            return rel

        def load_grm(ns, nv, path):
            m = np.zeros((ns, ns))
            with utils.hadoop_read(path) as f:
                i = 0
                for l in f:
                    row = l.strip().split('\t')
                    self.assertEqual(int(row[2]), nv)
                    m[int(row[0])-1, int(row[1])-1] = float(row[3])
                    i += 1

                self.assertEqual(i, ns * (ns + 1) / 2)
            return m

        def load_bin(ns, path):
            m = np.zeros((ns, ns))
            with utils.hadoop_read_binary(path) as f:
                for i in range(ns):
                    for j in range(i + 1):
                        b = f.read(4)
                        self.assertEqual(len(b), 4)
                        m[i, j] = unpack('<f', bytearray(b))[0]
                left = f.read()
                print(left)
                self.assertEqual(len(left), 0)
            return m

        b_file = utils.new_temp_file(prefix="plink")
        rel_file = utils.new_temp_file(prefix="test", suffix="rel")
        rel_id_file = utils.new_temp_file(prefix="test", suffix="rel.id")
        grm_file = utils.new_temp_file(prefix="test", suffix="grm")
        grm_bin_file = utils.new_temp_file(prefix="test", suffix="grm.bin")
        grm_nbin_file = utils.new_temp_file(prefix="test", suffix="grm.N.bin")

        dataset = self.get_dataset()
        n_samples = dataset.count_cols()
        dataset = dataset.annotate_rows(AC=agg.sum(dataset.GT.num_alt_alleles()),
                              n_called=agg.count_where(functions.is_defined(dataset.GT)))
        dataset = dataset.filter_rows((dataset.AC > 0) & (dataset.AC < 2 * dataset.n_called))
        dataset = dataset.filter_rows(dataset.n_called == n_samples).persist()

        dataset.to_hail1().export_plink(b_file)

        sample_ids = [row.s for row in dataset.cols_table().select('s').collect()]
        n_variants = dataset.count_rows()
        self.assertGreater(n_variants, 0)

        grm = methods.grm(dataset)
        grm.export_id_file(rel_id_file)

        ############
        ### rel

        p_file = utils.new_temp_file(prefix="plink")
        syscall('''plink --bfile {} --make-rel --out {}'''
                .format(utils.get_URI(b_file), utils.get_URI(p_file)), shell=True)
        self.assertEqual(load_id_file(p_file + ".rel.id"), sample_ids)

        grm.export_rel(rel_file)
        self.assertEqual(load_id_file(rel_id_file), sample_ids)
        self.assertTrue(np.allclose(load_rel(n_samples, p_file + ".rel"),
                           load_rel(n_samples, rel_file),
                           atol=tolerance))

        ############
        ### gcta-grm

        p_file = utils.new_temp_file(prefix="plink")
        syscall('''plink --bfile {} --make-grm-gz --out {}'''
                .format(utils.get_URI(b_file), utils.get_URI(p_file)), shell=True)
        self.assertEqual(load_id_file(p_file + ".grm.id"), sample_ids)

        grm.export_gcta_grm(grm_file)
        self.assertTrue(np.allclose(load_grm(n_samples, n_variants, p_file + ".grm.gz"),
                           load_grm(n_samples, n_variants, grm_file),
                           atol=tolerance))

        ############
        ### gcta-grm-bin

        p_file = utils.new_temp_file(prefix="plink")
        syscall('''plink --bfile {} --make-grm-bin --out {}'''
                .format(utils.get_URI(b_file), utils.get_URI(p_file)), shell=True)

        self.assertEqual(load_id_file(p_file + ".grm.id"), sample_ids)

        grm.export_gcta_grm_bin(grm_bin_file, grm_nbin_file)

        self.assertTrue(np.allclose(load_bin(n_samples, p_file + ".grm.bin"),
                           load_bin(n_samples, grm_bin_file),
                           atol=tolerance))
        self.assertTrue(np.allclose(load_bin(n_samples, p_file + ".grm.N.bin"),
                           load_bin(n_samples, grm_nbin_file),
                           atol=tolerance))

    def test_pca(self):
        dataset = hc._hc1.balding_nichols_model(3, 100, 100).to_hail2()
        eigenvalues, scores, loadings = methods.pca(dataset.GT.num_alt_alleles(), k=2, compute_loadings=True)

        self.assertEqual(len(eigenvalues), 2)
        self.assertTrue(isinstance(scores, Table))
        self.assertEqual(scores.count(), 100)
        self.assertTrue(isinstance(loadings, Table))
        self.assertEqual(loadings.count(), 100)

        _, _, loadings = methods.pca(dataset.GT.num_alt_alleles(), k=2, compute_loadings=False)
        self.assertEqual(loadings, None)

    def test_pcrelate(self):
        dataset = hc._hc1.balding_nichols_model(3, 100, 100).to_hail2()
        t = methods.pc_relate(dataset, 2, 0.05, block_size=64, statistics="phi")

        self.assertTrue(isinstance(t, Table))
        t.count()

    def test_rename_duplicates(self):
        dataset = self.get_dataset() # FIXME - want to rename samples with same id
        renamed_ids = methods.rename_duplicates(dataset).cols_table().select('s').collect()
        self.assertTrue(len(set(renamed_ids)), len(renamed_ids))

    def test_split_multi_hts(self):
        ds1 = hc.import_vcf('src/test/resources/split_test.vcf')
        ds1 = methods.split_multi_hts(ds1)
        ds2 = hc.import_vcf('src/test/resources/split_test_b.vcf')
        self.assertEqual(ds1.aggregate_entries(foo = agg.product((ds1.wasSplit == (ds1.v.start != 1180)).to_int32())).foo, 1)
        ds1 = ds1.drop('wasSplit','aIndex')
        # required python3
        # self.assertTrue(ds1._same(ds2))

    def test_mendel_errors(self):
        dataset = self.get_dataset()
        men, fam, ind, var = methods.mendel_errors(dataset, Pedigree.read('src/test/resources/sample.fam'))
        men.select('fid', 's', 'code')
        fam.select('father', 'nChildren')
        self.assertEqual(ind.key, ['s'])
        self.assertEqual(var.key, ['v'])
        dataset.annotate_rows(mendel=var[dataset.v]).count_rows()

    def test_export_vcf(self):
        dataset = hc.import_vcf('src/test/resources/sample.vcf.bgz')
        vcf_metadata = hc.get_vcf_metadata('src/test/resources/sample.vcf.bgz')
        methods.export_vcf(dataset, '/tmp/sample.vcf', metadata=vcf_metadata)
        dataset_imported = hc.import_vcf('/tmp/sample.vcf')
        self.assertTrue(dataset._same(dataset_imported))

        metadata_imported = hc.get_vcf_metadata('/tmp/sample.vcf')
        self.assertDictEqual(vcf_metadata, metadata_imported)

    def test_concordance(self):
        dataset = self.get_dataset()
        glob_conc, cols_conc, rows_conc = methods.concordance(dataset, dataset)

        self.assertEqual(sum([sum(glob_conc[i]) for i in range(5)]), dataset.count_rows() * dataset.count_cols())

        counts = dataset.aggregate_entries(nHet=agg.count(agg.filter(dataset.GT.is_het(), dataset.GT)),
                                           nHomRef=agg.count(agg.filter(dataset.GT.is_hom_ref(), dataset.GT)),
                                           nHomVar=agg.count(agg.filter(dataset.GT.is_hom_var(), dataset.GT)),
                                           nNoCall=agg.count(agg.filter(functions.is_missing(dataset.GT), dataset.GT)))

        self.assertEqual(glob_conc[0][0], 0)
        self.assertEqual(glob_conc[1][1], counts.nNoCall)
        self.assertEqual(glob_conc[2][2], counts.nHomRef)
        self.assertEqual(glob_conc[3][3], counts.nHet)
        self.assertEqual(glob_conc[4][4], counts.nHomVar)
        [self.assertEqual(glob_conc[i][j], 0) for i in range(5) for j in range(5) if i != j]

        self.assertTrue(cols_conc.forall(cols_conc.concordance.flatten().sum() == dataset.count_rows()))
        self.assertTrue(rows_conc.forall(rows_conc.concordance.flatten().sum() == dataset.count_cols()))

        cols_conc.write('/tmp/foo.kt', overwrite=True)
        rows_conc.write('/tmp/foo.kt', overwrite=True)

    def test_import_interval_list(self):
        interval_file = self.test_resources + '/annotinterall.interval_list'
        nint = methods.import_interval_list(interval_file).count()
        i = 0
        with open(interval_file) as f:
            for line in f:
                if len(line.strip()) != 0:
                    i += 1
        self.assertEqual(nint, i)

    def test_bed(self):
        bed_file = self.test_resources + '/example1.bed'
        nbed = methods.import_bed(bed_file).count()
        i = 0
        with open(bed_file) as f:
            for line in f:
                if len(line.strip()) != 0:
                    try:
                        int(line.split()[0])
                        i += 1
                    except:
                        pass
        self.assertEqual(nbed, i)

    def test_fam(self):
        fam_file = self.test_resources + '/sample.fam'
        nfam = methods.import_fam(fam_file).count()
        i = 0
        with open(fam_file) as f:
            for line in f:
                if len(line.strip()) != 0:
                    i += 1
        self.assertEqual(nfam, i)