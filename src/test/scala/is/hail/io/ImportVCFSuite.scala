package is.hail.io

import is.hail.SparkSuite
import is.hail.io.vcf.{HtsjdkRecordReader, LoadVCF, VCFReport}
import is.hail.variant.{Genotype, Variant}
import org.apache.spark.SparkException
import org.testng.annotations.Test

class ImportVCFSuite extends SparkSuite {

  @Test def testInfo() {
    assert(hc.importVCF("src/test/resources/infochar.vcf").countVariants() == 1)
  }

  @Test def lineRef() {

    val line1 = "20\t10280082\t.\tA\tG\t844.69\tPASS\tAC=1;..."
    assert(LoadVCF.lineRef(line1) == "A")

    val line2 = "20\t13561632\t.\tTAA\tT\t89057.4\tPASS\tAC=2;..."
    assert(LoadVCF.lineRef(line2) == "TAA")

    assert(LoadVCF.lineRef("") == "")

    assert(LoadVCF.lineRef("this\tis\ta") == "")

    assert(LoadVCF.lineRef("20\t0\t.\t") == "")

    assert(LoadVCF.lineRef("20\t0\t.\t\t") == "")

    assert(LoadVCF.lineRef("\t\t\tabcd") == "abcd")
  }

  @Test def symbolicOrSV() {
    val vds = hc.importVCF("src/test/resources/symbolicVariant.vcf")
    val n = vds.countVariants()

    assert(n == 1)
    assert(VCFReport.accumulators.head._2.value(VCFReport.Symbolic) == 2)
  }

  @Test def testStoreGQ() {
    var vds = hc.importVCF("src/test/resources/store_gq.vcf", storeGQ = true)

    val gqs = vds.flatMapWithKeys { case (v, s, g) =>
      g.gq.map { gqx => ((v.start, s), gqx) }
    }.collectAsMap()
    val expectedGQs = Map(
      (16050612, "S") -> 27,
      (16050612, "T") -> 15,
      (16051453, "S") -> 37,
      (16051453, "T") -> 52)
    assert(gqs == expectedGQs)

    vds = vds.splitMulti(propagateGQ = true)

    val f = tmpDir.createTempFile("store_gq", ".vcf")
    vds.exportVCF(f)

    hc.importVCF("src/test/resources/store_gq_split.vcf", storeGQ = true)
      .same(vds.eraseSplit)
  }

  @Test def testGlob() {

    val n1 = hc.importVCF("src/test/resources/sample.vcf").countVariants()
    val n2 = hc.importVCF("src/test/resources/samplepart*.vcf").countVariants()
    assert(n1 == n2)
  }

  @Test def testUndeclaredInfo() {
    val vds = hc.importVCF("src/test/resources/undeclaredinfo.vcf")

    assert(vds.vaSignature.getOption("info").isDefined)
    assert(vds.vaSignature.getOption("info", "undeclared").isEmpty)
    assert(vds.vaSignature.getOption("info", "undeclaredFlag").isEmpty)
    val infoQuerier = vds.vaSignature.query("info")

    val anno = vds
      .rdd
      .map { case (v, (va, gs)) => va }
      .collect()
      .head

    assert(infoQuerier(anno) != null)
  }

  @Test def testMalformed() {

    // FIXME abstract
    val e = intercept[SparkException] {
      hc.importVCF("src/test/resources/malformed.vcf").countVariants()
    }
    assert(e.getMessage.contains("caught htsjdk.tribble.TribbleException$InternalCodecException: "))
  }

  @Test def testPPs() {
    assert(hc.importVCF("src/test/resources/sample.PPs.vcf", ppAsPL = true)
      .same(hc.importVCF("src/test/resources/sample.vcf")))
  }

  @Test def testBadAD() {
    val vds = hc.importVCF("src/test/resources/sample_bad_AD.vcf", skipBadAD = true)
    assert(vds.expand()
      .map(_._3)
      .collect()
      .contains(Genotype(Some(0), None, Some(30), Some(72), Some(Array(0, 72, 1080)))))

    val failVDS = hc.importVCF("src/test/resources/sample_bad_AD.vcf")
    val e = intercept[SparkException] {
      failVDS.expand()
        .map(_._3)
        .collect()
        .contains(Genotype(Some(0), None, Some(30), Some(72), Some(Array(0, 72, 1080))))
    }
    assert(e.getMessage.contains("FatalException"))
  }

  @Test def testHaploid() {
    val vds = hc.importVCF("src/test/resources/haploid.vcf")
    val r = vds
      .expand()
      .collect()
      .map { case (v,s,g) => ((v,s), g)}
      .toMap

    hc.report()
    val v1 = Variant("X", 16050036, "A", "C")
    val v2 = Variant("X", 16061250, "T", Array("A", "C"))

    val s1 = "C1046::HG02024"
    val s2 = "C1046::HG02025"

    assert(r(v1, s1) == Genotype(
      Some(0),
      Some(Array(10, 0)),
      Some(10),
      Some(44),
      Some(Array(0,44,180))
    ))
    assert(r(v1, s2) == Genotype(
      Some(2),
      Some(Array(0,6)),
      Some(7),
      Some(70),
      Some(Array(70, HtsjdkRecordReader.haploidNonsensePL, 0))
    ))
    assert(r(v2, s1) == Genotype(
      Some(5),
      Some(Array(0,0,11)),
      Some(11),
      Some(33),
      Some(Array(396,402,411,33,33,0))
    ))
    assert(r(v2, s2) == Genotype(
      Some(5),
      Some(Array(0, 0, 9)),
      Some(9),
      Some(24),
      Some(Array(24, HtsjdkRecordReader.haploidNonsensePL, 40, HtsjdkRecordReader.haploidNonsensePL,
        HtsjdkRecordReader.haploidNonsensePL, 0))
    ))
  }
}
