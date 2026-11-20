from __future__ import annotations

import pytest

from conftest import make_wheel, write_vendor
import vendor


def test_repository_vendor_dir_is_valid():
    infos = vendor.verify()
    names = {i.name for i in infos}
    assert "textual" in names and "rich" in names


def test_valid_fake_vendor(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", requires=("beta>=1",))
    make_wheel(fake_vendor / "wheels", "beta", license_expression="BSD-3-Clause")
    write_vendor(fake_vendor, "alpha==1.0\n")
    assert [i.name for i in vendor.verify(fake_vendor)] == ["alpha", "beta"]


def test_rejects_disallowed_license(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", license_expression="GPL-3.0-only")
    write_vendor(fake_vendor, "alpha==1.0\n", licenses={"alpha": "GPL-3.0-only"})
    with pytest.raises(vendor.VendorError, match="GPL-3.0-only' is not allowed"):
        vendor.verify(fake_vendor)


def test_rejects_ambiguous_bsd_classifier_unless_overridden(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", license_expression=None,
               classifiers=("License :: OSI Approved :: BSD License",))
    write_vendor(fake_vendor, "alpha==1.0\n", licenses={"alpha": "BSD-3-Clause"})
    with pytest.raises(vendor.VendorError, match="license not detectable"):
        vendor.verify(fake_vendor)
    write_vendor(fake_vendor, "# license: alpha = BSD-3-Clause\nalpha==1.0\n", licenses={"alpha": "BSD-3-Clause"})
    vendor.verify(fake_vendor)


def test_rejects_native_wheel_tag(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", tag="cp313-cp313-win_amd64")
    write_vendor(fake_vendor, "alpha==1.0\n")
    with pytest.raises(vendor.VendorError, match="not a pure-Python wheel"):
        vendor.verify(fake_vendor)


def test_rejects_native_code_in_pure_wheel(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", extra_files={"alpha/_speedups.so": b"\x7fELF"})
    write_vendor(fake_vendor, "alpha==1.0\n")
    with pytest.raises(vendor.VendorError, match="contains native code"):
        vendor.verify(fake_vendor)


def test_rejects_hash_mismatch(fake_vendor):
    whl = make_wheel(fake_vendor / "wheels", "alpha")
    write_vendor(fake_vendor, "alpha==1.0\n")
    whl.write_bytes(whl.read_bytes() + b"tampered")
    with pytest.raises(vendor.VendorError, match="sha256 mismatch"):
        vendor.verify(fake_vendor)


def test_rejects_missing_license_file(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", license_file=False)
    write_vendor(fake_vendor, "alpha==1.0\n")
    with pytest.raises(vendor.VendorError, match="no license file"):
        vendor.verify(fake_vendor)


def test_rejects_missing_dependency_unless_ignored(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", requires=("beta",))
    write_vendor(fake_vendor, "alpha==1.0\n")
    with pytest.raises(vendor.VendorError, match="beta: required but not vendored"):
        vendor.verify(fake_vendor)
    write_vendor(fake_vendor, "# ignore: beta\nalpha==1.0\n")
    vendor.verify(fake_vendor)


def test_rejects_wheel_not_in_lock(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha")
    write_vendor(fake_vendor, "alpha==1.0\n")
    make_wheel(fake_vendor / "wheels", "stray")
    with pytest.raises(vendor.VendorError, match="stray-1.0-py3-none-any.whl: in wheels/ but not in vendor.lock"):
        vendor.verify(fake_vendor)


def test_extras_and_python_markers(fake_vendor):
    make_wheel(fake_vendor / "wheels", "alpha", requires=(
        'beta; extra == "fancy"',
        'oldcompat; python_version < "3.11"',
    ))
    write_vendor(fake_vendor, "alpha==1.0\n")
    vendor.verify(fake_vendor)  # neither extra requested nor old Python supported
    write_vendor(fake_vendor, "alpha[fancy]==1.0\n")
    with pytest.raises(vendor.VendorError, match="beta: required"):
        vendor.verify(fake_vendor)


@pytest.mark.parametrize(("expr", "allowed"), [
    ("MIT", True),
    ("BSD-2-Clause", True),
    ("MIT OR GPL-3.0-only", True),
    ("(MIT AND GPL-3.0-only)", False),
    ("LGPL-2.1-or-later", False),
    ("MPL-2.0", False),
])
def test_license_expressions(expr, allowed):
    assert vendor.license_allowed(expr) is allowed
