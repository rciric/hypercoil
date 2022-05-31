# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Unit tests for polynomial convolution
"""
import pytest
import torch
from hypercoil.functional import (
    polyconv2d, basisconv2d
)


def known_filter():
    weight = torch.Tensor([
        [0, 0, 1, 0, 0],
        [0, 0, 0.3, 0, 0],
        [0, 0, -0.1, 0, 0]
    ])
    return weight.view(1, weight.size(0), 1, weight.size(1))


class TestPolynomial:

    @pytest.fixture(autouse=True)
    def setup_class(self):
        self.X = torch.rand(7, 100)
        self.approx = torch.allclose
        if torch.cuda.is_available():
            self.XC = self.X.clone().cuda()

    def test_polyconv2d(self):
        out = polyconv2d(self.X, known_filter())
        ref = self.X + 0.3 * self.X ** 2 - 0.1 * self.X ** 3
        assert self.approx(out, ref)

    @pytest.mark.cuda
    def test_polyconv2d_cuda(self):
        out = polyconv2d(self.XC, known_filter().cuda())
        ref = self.XC + 0.3 * self.XC ** 2 - 0.1 * self.XC ** 3
        assert self.approx(out, ref)

    def test_basisconv2d(self):
        basis = [
            (lambda x: x ** 1),
            (lambda x: x ** 2),
            (lambda x: x ** 3),
        ]
        out = basisconv2d(
            self.X,
            basis_functions=basis,
            weight=known_filter()
        )
        ref = self.X + 0.3 * self.X ** 2 - 0.1 * self.X ** 3
        assert self.approx(out, ref)
