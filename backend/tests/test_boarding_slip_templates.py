"""Boarding-slip template paths must resolve to the shipped PDFs.

Regression: the services-package reorg (daee081) moved boarding_slip.py one
level deeper but left the asset path at parents[1], so it pointed at the
non-existent app/services/assets/ and every boarding slip 500'd with
"template missing". This guards the resolved paths so a future move can't
silently break generation again.
"""
import os

from app.services.surgery.boarding_slip import CRMC_TEMPLATE, MEDSTAR_TEMPLATE


def test_medstar_template_exists():
    assert os.path.exists(MEDSTAR_TEMPLATE), MEDSTAR_TEMPLATE


def test_crmc_template_exists():
    assert os.path.exists(CRMC_TEMPLATE), CRMC_TEMPLATE
