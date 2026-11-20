from __future__ import annotations

import make_candbpp_check

from dbc2h.core.dbcfile import DbcFile
from dbc2h.core import settings


def test_committed_check_files_match_the_generator():
    for path, data in make_candbpp_check.outputs().items():
        assert path.read_bytes() == data, f"{path.name} veraltet: python tools/make_candbpp_check.py"


def test_check_files_load_back_with_all_settings():
    for path in make_candbpp_check.outputs():
        f = DbcFile.load(path)
        net = f.document.network
        assert settings.conflicts(net) == []
        assert settings.get_setting(net, net, "dbc2h_FloatType") == "double"
        assert settings.get_setting(net, net, "dbc2h_StrictQuantization") == 1
        status = net.message_by_name("VehicleStatus")
        assert status.comment == make_candbpp_check.BACKSLASH_COMMENT
        assert settings.get_setting(net, status.signals[0], "dbc2h_SigFloatType") == "float"
        assert {d.name for d in net.attribute_definitions} == set(settings.SETTINGS)
