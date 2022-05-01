# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Unit tests for specialised matrix operations
"""
import pytest
import torch
import numpy as np
from scipy.linalg import toeplitz as toeplitz_ref
from hypercoil.functional import (
    invert_spd,
    toeplitz,
    symmetric,
    spd,
    expand_outer,
    recondition_eigenspaces,
    delete_diagonal,
    sym2vec,
    vec2sym,
    squareform
)


class TestMatrix:

    @pytest.fixture(autouse=True)
    def setup_class(self):
        self.tol = 5e-3
        self.approx = lambda out, ref: np.allclose(out, ref, atol=self.tol)
        np.random.seed(10)
        torch.manual_seed(10)


        A = np.random.rand(10, 10)
        self.A = A @ A.T
        self.c = np.random.rand(3)
        self.r = np.random.rand(3)
        self.C = np.random.rand(3, 3)
        self.R = np.random.rand(4, 3)
        self.f = np.random.rand(3)
        self.At = torch.Tensor(self.A)
        self.ct = torch.Tensor(self.c)
        self.rt = torch.Tensor(self.r)
        self.Ct = torch.Tensor(self.C)
        self.Rt = torch.Tensor(self.R)
        self.ft = torch.Tensor(self.f)
        self.Bt = torch.rand(20, 10, 10)
        BLRt = torch.rand(20, 10, 2)
        self.BLRt = BLRt @ BLRt.transpose(-1, -2)

        if torch.cuda.is_available():
            self.AtC = self.At.clone().cuda()
            self.BtC = self.Bt.clone().cuda()
            self.BLRtC = self.BLRt.clone().cuda()
            self.ctC = self.ct.clone().cuda()
            self.rtC = self.rt.clone().cuda()
            self.CtC = self.Ct.clone().cuda()
            self.RtC = self.Rt.clone().cuda()
            self.ftC = self.ft.clone().cuda()

    def test_invert_spd(self):
        out = invert_spd(invert_spd(self.At))
        ref = self.At
        assert torch.allclose(out, ref, atol=1e-2)

    def test_symmetric(self):
        out = symmetric(self.Bt)
        ref = out.transpose(-1, -2)
        assert self.approx(out, ref)

    def test_expand_outer(self):
        L = torch.rand(8, 4, 10, 3)
        C = -torch.rand(8, 4, 3, 1)
        out = expand_outer(L, C=C)
        assert torch.all(out <= 0)
        C = torch.diag_embed(C.squeeze())
        out2 = expand_outer(L, C=C)
        assert torch.allclose(out, out2)

    def test_spd(self):
        out = spd(self.Bt)
        ref = out.transpose(-1, -2)
        assert self.approx(out, ref)
        L = torch.linalg.eigvalsh(out)
        assert torch.all(L > 0)

    def test_spd_singular(self):
        out = spd(self.BLRt, method='eig')
        ref = out.transpose(-1, -2)
        assert self.approx(out, ref)
        L = torch.linalg.eigvalsh(out)
        assert torch.all(L > 0)

    def test_toeplitz(self):
        out = toeplitz(self.ct, self.rt).numpy()
        ref = toeplitz_ref(self.c, self.r)
        assert self.approx(out, ref)

    def test_toeplitz_stack(self):
        out = toeplitz(self.Ct, self.Rt).numpy()
        ref = np.stack([toeplitz_ref(c, r)
                        for c, r in zip(self.C.T, self.R.T)])
        assert self.approx(out, ref)

    def test_toeplitz_extend(self):
        dim = (8, 8)
        out = toeplitz(self.Ct, self.Rt, dim=dim)
        Cx, Rx = (np.zeros((dim[0], self.C.shape[1])),
                  np.zeros((dim[1], self.R.shape[1])))
        Cx[:self.C.shape[0], :] = self.C
        Rx[:self.R.shape[0], :] = self.R
        ref = np.stack([toeplitz_ref(c, r) for c, r in zip(Cx.T, Rx.T)])
        assert self.approx(out, ref)

    def test_toeplitz_fill(self):
        dim = (8, 8)
        out = toeplitz(self.Ct, self.Rt, dim=dim, fill_value=self.ft)
        Cx, Rx = (np.zeros((dim[0], self.C.shape[1])) + self.f,
                  np.zeros((dim[1], self.R.shape[1])) + self.f)
        Cx[:self.C.shape[0], :] = self.C
        Rx[:self.R.shape[0], :] = self.R
        ref = np.stack([toeplitz_ref(c, r) for c, r in zip(Cx.T, Rx.T)])
        assert self.approx(out, ref)

    def test_recondition(self):
        V = torch.ones((7, 3))
        V.requires_grad = True
        (V @ V.t()).svd()[0].sum().backward()
        assert torch.all(torch.isnan(V.grad))
        V.grad.zero_()

        recondition_eigenspaces(
            V @ V.t(), psi=1e-3, xi=1e-3
        ).svd()[0].sum().backward()
        assert torch.logical_not(torch.any(torch.isnan(V.grad)))

    def test_sym2vec_correct(self):
        from scipy.spatial.distance import squareform
        K = symmetric(torch.rand(3, 4, 5, 5))
        out = sym2vec(K)

        ref = np.stack([
            np.stack([
                squareform(j.numpy() * (1 - np.eye(j.shape[0])))
                for j in k
            ]) for k in K
        ])
        assert np.allclose(out, ref)

    def test_sym2vec_inversion(self):
        K = symmetric(torch.rand(3, 4, 5, 5))
        out = vec2sym(sym2vec(K, offset=0), offset=0)
        assert torch.allclose(out, K)

    def test_squareform_equivalence(self):
        K = symmetric(torch.rand(3, 4, 5, 5))
        out = squareform(K)
        ref = sym2vec(K)
        assert torch.allclose(out, ref)

        out = squareform(out)
        ref = vec2sym(ref)
        assert torch.allclose(out, ref)

    @pytest.mark.cuda
    def test_invert_spd_cuda(self):
        out = invert_spd(invert_spd(self.AtC))
        ref = self.AtC
        assert self.approx(out.cpu(), ref)

    @pytest.mark.cuda
    def test_symmetric_cuda(self):
        out = symmetric(self.BtC)
        ref = out.transpose(-1, -2)
        assert self.approx(out.cpu(), ref.cpu())

    @pytest.mark.cuda
    def test_spd_cuda(self):
        out = spd(self.BtC)
        ref = out.transpose(-1, -2)
        assert self.approx(out.clone().cpu(), ref.cpu())
        L = torch.linalg.eigvalsh(out)
        assert torch.all(L > 0)

    @pytest.mark.cuda
    def test_spd_singular_cuda(self):
        out = spd(self.BLRtC, method='eig')
        ref = out.transpose(-1, -2)
        assert self.approx(out.clone().cpu(), ref.cpu())
        L = torch.linalg.eigvalsh(out)
        assert torch.all(L > 0)

    @pytest.mark.cuda
    def test_toeplitz_cuda(self):
        out = toeplitz(self.ctC, self.rtC).cpu().numpy()
        ref = toeplitz_ref(self.c, self.r)
        assert self.approx(out, ref)

    @pytest.mark.cuda
    def test_toeplitz_stack_cuda(self):
        out = toeplitz(self.CtC, self.RtC).cpu().numpy()
        ref = np.stack([toeplitz_ref(c, r)
                        for c, r in zip(self.C.T, self.R.T)])
        assert self.approx(out, ref)

    @pytest.mark.cuda
    def test_toeplitz_extend_cuda(self):
        dim = (8, 8)
        out = toeplitz(self.CtC, self.RtC, dim=dim)
        Cx, Rx = (np.zeros((dim[0], self.C.shape[1])),
                  np.zeros((dim[1], self.R.shape[1])))
        Cx[:self.C.shape[0], :] = self.C
        Rx[:self.R.shape[0], :] = self.R
        ref = np.stack([toeplitz_ref(c, r) for c, r in zip(Cx.T, Rx.T)])
        assert self.approx(out.cpu(), ref)

    @pytest.mark.cuda
    def test_toeplitz_fill_cuda(self):
        dim = (8, 8)
        out = toeplitz(self.CtC, self.RtC, dim=dim, fill_value=self.ftC)
        Cx, Rx = (np.zeros((dim[0], self.C.shape[1])) + self.f,
                  np.zeros((dim[1], self.R.shape[1])) + self.f)
        Cx[:self.C.shape[0], :] = self.C
        Rx[:self.R.shape[0], :] = self.R
        ref = np.stack([toeplitz_ref(c, r) for c, r in zip(Cx.T, Rx.T)])
        assert self.approx(out.cpu(), ref)

    @pytest.mark.cuda
    def test_recondition_cuda(self):
        V = torch.ones((7, 3), device='cuda')
        V.requires_grad = True
        (V @ V.t()).svd()[0].sum().backward()
        assert torch.all(torch.isnan(V.grad))
        V.grad.zero_()

        recondition_eigenspaces(
            V @ V.t(), psi=1e-3, xi=1e-3
        ).svd()[0].sum().backward()
        assert torch.logical_not(torch.any(torch.isnan(V.grad)))

    @pytest.mark.cuda
    def test_sym2vec_correct_cuda(self):
        from scipy.spatial.distance import squareform
        K = symmetric(torch.rand(3, 4, 5, 5, device='cuda'))
        out = sym2vec(K)

        ref = np.stack([
            np.stack([
                squareform(j.numpy() * (1 - np.eye(j.shape[0])))
                for j in k
            ]) for k in K.cpu()
        ])
        assert np.allclose(out.cpu(), ref)

    @pytest.mark.cuda
    def test_sym2vec_inversion_cuda(self):
        K = symmetric(torch.rand(3, 4, 5, 5, device='cuda'))
        out = vec2sym(sym2vec(K, offset=0), offset=0)
        assert torch.allclose(out, K)

    @pytest.mark.cuda
    def test_squareform_equivalence_cuda(self):
        K = symmetric(torch.rand(3, 4, 5, 5, device='cuda'))
        out = squareform(K)
        ref = sym2vec(K)
        assert torch.allclose(out, ref)

        out = squareform(out)
        ref = vec2sym(ref)
        assert torch.allclose(out, ref)
