# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Unit tests for frequency-domain filter layer
"""
import pytest
import numpy as np
import torch
from hypernova.nn import FrequencyDomainFilter
from hypernova.functional.domain import Identity
from hypernova.init.iirfilter import (
    IIRFilterSpec,
    iirfilter_init_,
    clamp_init_
)


class TestFreqFilter:

    @pytest.fixture(autouse=True)
    def setup_class(self):
        N = torch.Tensor([[1], [4]])
        Wn = torch.Tensor([[0.1, 0.3], [0.4, 0.6]])
        self.filter_specs = [
            IIRFilterSpec(Wn=[0.1, 0.3]),
            IIRFilterSpec(Wn=Wn, N=N),
            IIRFilterSpec(Wn=Wn, ftype='ideal'),
            IIRFilterSpec(Wn=[0.1, 0.2], N=[2, 2], btype='lowpass'),
            IIRFilterSpec(Wn=Wn, N=N, ftype='cheby1', rp=0.1),
            IIRFilterSpec(Wn=Wn, N=N, ftype='cheby2', rs=20),
            IIRFilterSpec(Wn=Wn, N=N, ftype='ellip', rs=20, rp=0.1)
        ]
        self.clamped_specs = [
            IIRFilterSpec(Wn=[0.1, 0.3]),
            IIRFilterSpec(Wn=Wn, clamps=[{}, {0.1: 1}]),
            IIRFilterSpec(Wn=[0.1, 0.3], clamps=[{0.1: 0, 0.5:1}]),
            IIRFilterSpec(Wn=Wn, N=N, clamps=[{0.05: 1, 0.1: 0},
                                              {0.2: 0, 0.5: 1}])
        ]
        self.identity_spec = [
            IIRFilterSpec(Wn=[0, 1], ftype='ideal')
        ]
        self.Z1 = torch.rand(99)
        self.Z2 = torch.rand(1, 99)
        self.Z3 = torch.rand(7, 99)
        self.Z4 = torch.rand(1, 7, 99)

        self.approx = torch.allclose

    def test_shape_forward(self):
        filt = FrequencyDomainFilter(self.filter_specs, dim=50)
        out = filt(self.Z1)
        assert out.size() == torch.Size([13, 99])
        out = filt(self.Z2)
        assert out.size() == torch.Size([13, 99])
        out = filt(self.Z3)
        assert out.size() == torch.Size([13, 7, 99])
        out = filt(self.Z4)
        assert out.size() == torch.Size([1, 13, 7, 99])

    def test_shape_clamped_forward(self):
        filt = FrequencyDomainFilter(self.clamped_specs, dim=50)
        out = filt(self.Z1)
        assert out.size() == torch.Size([6, 99])
        out = filt(self.Z2)
        assert out.size() == torch.Size([6, 99])
        out = filt(self.Z3)
        assert out.size() == torch.Size([6, 7, 99])
        out = filt(self.Z4)
        assert out.size() == torch.Size([1, 6, 7, 99])

    def test_identity_forward(self):
        filt = FrequencyDomainFilter(self.identity_spec, dim=50,
                                     domain=Identity())
        out = filt(self.Z1)
        assert (out - self.Z1).max() < 1e-5
