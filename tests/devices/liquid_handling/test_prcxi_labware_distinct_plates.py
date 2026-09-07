from unilabos.devices.liquid_handling.prcxi.prcxi_labware import (
    PRCXI_96_DeepWell,
    PRCXI_96_DeepWellhplc,
    PRCXI_nest_1_troughplate,
    PRCXI_nest_1_troughplate1,
    PRCXI_nest_1_troughplate2,
    PRCXI_1000uL_Tips,
    PRCXI_1000uL_Tips1,
)


def test_reaction_and_hplc_deep_wells_have_distinct_resource_classes() -> None:
    reaction_plate = PRCXI_96_DeepWell("PRCXI_96_DeepWell_反应板")
    hplc_plate = PRCXI_96_DeepWellhplc("PRCXI_96_DeepWellhplc")

    assert reaction_plate.model == "PRCXI_96_DeepWell"
    assert hplc_plate.model == "PRCXI_96_DeepWellhplc"
    assert reaction_plate.unilabos_extra["unilabos_resource_class"] == "PRCXI_96_DeepWell"
    assert hplc_plate.unilabos_extra["unilabos_resource_class"] == "PRCXI_96_DeepWellhplc"
    assert len(reaction_plate.children) == len(hplc_plate.children) == 96


def test_nest_troughplates_have_distinct_resource_classes() -> None:
    troughplate = PRCXI_nest_1_troughplate("PRCXI_nest_1_troughplate")
    troughplate1 = PRCXI_nest_1_troughplate1("自定义储液槽一")
    troughplate2 = PRCXI_nest_1_troughplate2("自定义储液槽二")

    assert troughplate.model == "PRCXI_nest_1_troughplate"
    assert troughplate1.model == "PRCXI_nest_1_troughplate1"
    assert troughplate2.model == "PRCXI_nest_1_troughplate2"
    assert troughplate.unilabos_extra["unilabos_resource_class"] == "PRCXI_nest_1_troughplate"
    assert troughplate1.unilabos_extra["unilabos_resource_class"] == "PRCXI_nest_1_troughplate1"
    assert troughplate2.unilabos_extra["unilabos_resource_class"] == "PRCXI_nest_1_troughplate2"
    assert len(troughplate.children) == len(troughplate1.children) == len(troughplate2.children) == 1


def test_1000ul_tip_racks_have_distinct_resource_classes() -> None:
    tips = PRCXI_1000uL_Tips("自定义 1000uL 枪头盒")
    tips1 = PRCXI_1000uL_Tips1("另一个 1000uL 枪头盒")

    assert tips.model == "PRCXI_1000uL_Tips"
    assert tips1.model == "PRCXI_1000uL_Tips1"
    assert tips.unilabos_extra["unilabos_resource_class"] == "PRCXI_1000uL_Tips"
    assert tips1.unilabos_extra["unilabos_resource_class"] == "PRCXI_1000uL_Tips1"
    assert len(tips.children) == len(tips1.children) == 96
