# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Unit tests for noise sources
"""
import pytest
import numpy as np
import torch
from hypercoil.functional import (
    SPSDNoiseSource,
    LowRankNoiseSource,
    BandDropoutSource,
    UnstructuredNoiseSource
)


def lr_std_mean(dim=100, rank=None, std=0.05, iter=1000):
    distr = torch.distributions.normal.Normal(
        torch.Tensor([0]), torch.Tensor([std]))
    lrns = LowRankNoiseSource(rank=rank, distr=distr)
    return torch.Tensor(
        [lrns.sample([dim]).std() for _ in range(iter)
    ]).mean()


class TestNoise:

    @pytest.fixture(autouse=True)
    def setup_class(self):
        self.atol = 1e-3
        self.rtol = 1e-4
        self.approx = lambda out, ref: np.isclose(
            out, ref, atol=self.atol, rtol=self.rtol)

    def test_lr_std(self):
        out = lr_std_mean()
        ref = 0.05
        assert self.approx(out, ref)
        out = lr_std_mean(std=0.2)
        ref = 0.2
        assert self.approx(out, ref)
        out = lr_std_mean(std=0.03, rank=7)
        ref = 0.03
        assert self.approx(out, ref)

    def test_spsd_spsd(self):
        spsdns = SPSDNoiseSource()
        out = spsdns.sample([100])
        assert np.allclose(out, out.T, atol=1e-5)
        # ignore effectively-zero eigenvalues
        L = np.linalg.eigvals(out)
        L[np.abs(L) < 1e-5] = 0
        assert L.min() >= 0
        assert np.all(L >= 0)

    def test_band_correction(self):
        bds = BandDropoutSource()
        out = bds.sample([100]).sum()
        ref = bds.bandmask.sum()
        assert torch.abs((out - ref) / ref) <= 0.2

    def test_scalar_iid_noise(self):
        sz = torch.Size([3, 8, 1, 21, 1])
        inp = torch.rand(sz)
        sins = UnstructuredNoiseSource()
        out = sins(inp)
        assert out.size() == sz