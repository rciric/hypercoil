# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Parameterised similarity kernels and distance metrics.
"""
import torch
import jax
import jax.numpy as jnp
from functools import singledispatch
from typing import Optional
from jax.experimental.sparse import BCOO
from .sparse import TopKTensor, full_as_topk, spspmm, spdiagmm, topkx
from .utils import (
    Tensor, is_sparse,
    sparse_mm, sparse_rcmul, sparse_reciprocal,
    _conform_vector_weight
)


def _default_gamma(X, gamma):
    if gamma is None:
        gamma = 1 / X.shape[-1]
    return gamma


@singledispatch
def linear_kernel(
    X0: Tensor,
    X1: Optional[Tensor] = None,
    theta: Optional[Tensor] = None,
    intermediate_indices: None = None,
) -> Tensor:
    r"""
    Parameterised linear kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised linear kernel is

    :math:`K_{\theta}(X_0, X_1) = X_0^\intercal \theta X_1`

    where :math:`\theta` is the kernel parameter.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    if X1 is None:
        X1 = X0
    if theta is None:
        return X0 @ X1.swapaxes(-1, -2)
    elif theta.ndim == 1 or theta.shape[-1] != theta.shape[-2]:
        theta = _conform_vector_weight(theta)
        return (X0 * theta) @ X1.swapaxes(-1, -2)
    else:
        return X0 @ theta @ X1.swapaxes(-1, -2)


@linear_kernel.register
def _(
    X0: TopKTensor,
    X1: Optional[TopKTensor] = None,
    theta: Optional[Tensor] = None,
    intermediate_indices: Optional[Tensor] = None,
) -> TopKTensor:
    if X1 is None:
        X1 = X0
    if theta is None:
        return spspmm(X0, X1)
    elif is_sparse(theta):
        if theta.data.shape[-1] == theta.shape[-1]:
            lhs = X0
            if intermediate_indices is not None:
                rhs = topkx(spspmm)(intermediate_indices, X1, theta)
            else:
                rhs = spspmm(X1, theta)
        elif intermediate_indices is not None:
            mm = topkx(spspmm)
            lhs = mm(intermediate_indices, X0, theta)
            rhs = mm(intermediate_indices, X1, theta)
        else:
            lhs = spspmm(X0, theta)
            rhs = spspmm(X1, theta)
        return spspmm(lhs, rhs)
    elif theta.ndim == 1 or theta.shape[-1] != theta.shape[-2]:
        return spspmm(spdiagmm(X0, theta), X1)
    else:
        theta = full_as_topk(theta)
        if intermediate_indices is not None:
            rhs = topkx(spspmm)(intermediate_indices, X1, theta)
            return spspmm(X0, rhs)
        return spspmm(X0, spspmm(X1, theta))


def _param_norm_sparse(X, theta, squared=False):
    if theta is None:
        out = torch.sparse.sum(X ** 2, dim=1)
        if squared:
            return out
        return out.sqrt()
    elif theta.is_sparse:
        pass
    elif theta.dim() == 1 or theta.shape[-1] != theta.shape[-2]:
        theta = _embed_params_in_diagonal(theta)
    else:
        theta = _embed_params_in_sparse(theta)
    sc = (sparse_mm(X, theta) * X)
    out = torch.sparse.sum(sc, dim=1)
    if squared:
        return out
    return out.sqrt()


def _param_norm_sparse(X, theta, squared=False):
    if theta is None:
        data = X.data.sum(-1, keepdims=True)
        if not squared:
            data = jnp.sqrt(data)
    elif is_sparse(theta):
        pass


def _param_norm(X, theta, squared=False):
    if X.is_sparse:
        return _param_norm_sparse(X, theta, squared)
    elif squared:
        return linear_distance(X, X, theta)
    else:
        return jnp.sqrt(linear_distance(X, X, theta))


def linear_distance(X0, X1=None, theta=None):
    """Squared Euclidean (L2) distance."""
    if X1 is None:
        X1 = X0
    D = X0[..., None, :] - X1[..., None, :, :]
    D = linear_kernel(X0=D[..., None, :], theta=theta)
    return D.reshape(*D.shape[:-2])


def polynomial_kernel(X0, X1=None, theta=None, gamma=None, order=3, r=0):
    r"""
    Parameterised polynomial kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised polynomial kernel is

    :math:`K_{\theta}(X_0, X_1) = (\gamma X_0^\intercal \theta X_1 + r)^\omega`

    where :math:`\theta` is the kernel parameter, :math:`\gamma` and r are
    scaling and offset coefficients, and :math:`\omega` is the maximum degree
    or order of the kernel.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.
    gamma : float or None (default None)
        Scaling coefficient. If not explicitly specified, this is
        automatically set to :math:`\frac{1}{P}`.
    order : int (default 3)
        Maximum degree of the polynomial.
    r : float (default 0)
        Offset coefficient.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    gamma = _default_gamma(X0, gamma)
    K = linear_kernel(X0, X1, theta)
    return (gamma * K + r) ** order


def sigmoid_kernel(X0, X1=None, theta=None, gamma=None, r=0):
    r"""
    Parameterised sigmoid kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised sigmoid kernel is

    :math:`K_{\theta}(X_0, X_1) = \tanh (\gamma X_0^\intercal \theta X_1 + r)`

    where :math:`\theta` is the kernel parameter, and :math:`\gamma` and r are
    scaling and offset coefficients.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.
    gamma : float or None (default None)
        Scaling coefficient. If not explicitly specified, this is
        automatically set to :math:`\frac{1}{P}`.
    r : float (default 0)
        Offset coefficient.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    gamma = _default_gamma(X0, gamma)
    K = linear_kernel(X0, X1, theta)
    return jax.nn.tanh(gamma * K + r)


def gaussian_kernel(X0, X1=None, theta=None, sigma=None):
    r"""
    Parameterised Gaussian kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised Gaussian kernel is

    :math:`K_{\theta}(X_0, X_1) = e^{\frac{1}{\sigma^2} (X_0 - X_1)^\intercal \theta (X_0 - X_1)}`

    where :math:`\theta` is the kernel parameter, :math:`\sigma` is an
    isotropic standard deviation, and :math:`X_0 - X_1` contains all pairwise
    differences between vectors in :math:`X_0` and :math:`X_1`. The kernel
    parameter :math:`\theta` can also be interpreted as an inverse covariance.

    This is the same as :func:`rbf_kernel` but is parameterised in terms of
    :math:`\sigma` rather than  :math:`\gamma`.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.
    gamma : float or None (default None)
        Scaling coefficient. If not explicitly specified, this is
        automatically set to :math:`\frac{1}{P}`.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    if sigma is not None:
        gamma = sigma ** -2
    else:
        gamma = sigma
    return rbf_kernel(X0, X1, theta=theta, gamma=gamma)


def rbf_kernel(X0, X1=None, theta=None, gamma=None):
    r"""
    Parameterised RBF kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised RBF kernel is

    :math:`K_{\theta}(X_0, X_1) = e^{\gamma (X_0 - X_1)^\intercal \theta (X_0 - X_1)}`

    where :math:`\theta` is the kernel parameter, :math:`\gamma` is a scaling
    coefficient, and :math:`X_0 - X_1` contains all pairwise differences
    between vectors in :math:`X_0` and :math:`X_1`. The kernel parameter
    :math:`\theta` can also be interpreted as an inverse covariance.

    This is the same as :func:`gaussian_kernel` but is parameterised in terms
    of :math:`\gamma` rather than  :math:`\sigma`.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.
    gamma : float or None (default None)
        Scaling coefficient. If not explicitly specified, this is
        automatically set to :math:`\frac{1}{P}`.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    gamma = _default_gamma(X0, gamma)
    K = linear_distance(X0, X1, theta)
    return jnp.exp(-gamma * K)


def cosine_kernel(X0, X1=None, theta=None):
    r"""
    Parameterised cosine kernel between input tensors.

    For tensors :math:`X_0` and :math:`X_1` containing features in column
    vectors, the parameterised cosine kernel is

    :math:`K_{\theta}(X_0, X_1) = \frac{X_0^\intercal \theta X_1}{\|X_0\|_\theta \|X_1\|_\theta}`

    where the parameterised norm vector

    :math:`\|A\|_{\theta;i} = \sqrt{A_i^\intercal \theta A_i}`

    is the elementwise square root of the vector of quadratic forms.

    .. note::
        The inputs here are assumed to contain features in row vectors and
        observations in columns. This differs from the convention frequently
        used in the literature. However, this has the benefit of direct
        compatibility with the top-k sparse tensor format.

    :Dimension: **X0 :** :math:`(*, N, P)` or :math:`(N, P, *)`
                    N denotes number of observations, P denotes number of
                    features, `*` denotes any number of additional dimensions.
                    If the input is dense, then the last dimensions should be
                    N and P; if it is sparse, then the first dimensions should
                    be N and P.
                **X1 :** :math:`(*, M, P)` or  :math:`(M, P, *)`
                    M denotes number of observations.
                **theta :** :math:`(*, P, P)` or :math:`(*, P)`
                    As above.
                **Output :** :math:`(*, M, N)` or :math:`(M, N, *)`
                    As above.

    Parameters
    ----------
    X0 : tensor
        A feature tensor.
    X1 : tensor or None
        Second feature tensor. If not explicitly provided, the kernel of
        ``X`` with itself is computed.
    theta : tensor or None
        Kernel parameter (generally a representation of a positive definite
        matrix). If not provided, defaults to identity (an unparameterised
        kernel). If the last two dimensions are the same size, they are used
        as a matrix parameter; if they are not, the final axis is instead
        used as the diagonal of the matrix.
    gamma : float or None (default None)
        Scaling coefficient. If not explicitly specified, this is
        automatically set to :math:`\frac{1}{P}`.

    Returns
    -------
    tensor
        Kernel Gram matrix.
    """
    if X1 is None:
        X1 = X0
    X0_norm = _param_norm(X0, theta)
    X1_norm = _param_norm(X1, theta)
    num = linear_kernel(X0, X1, theta)
    if num.is_sparse:
        return sparse_rcmul(
            num,
            sparse_reciprocal(X0_norm),
            sparse_reciprocal(X1_norm),
        )
    return num * (X0_norm.unsqueeze(1) * X1_norm.unsqueeze(0)).reciprocal()
