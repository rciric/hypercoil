# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Utilities for operations on various BCOO sparse matrix formats.

Top-k BCOO format
-----------------

One common BCOO sparse format that is useful in many applications, such as
connectopic mapping, is the top-k format. This format is a sparse matrix with
the following properties:
- Each row has no more than k non-zero entries.
- The indices of nonzero entries are shared across all batch elements.
- The indexing tensor has shape (..., ``n_rows``, ``k``, 1) where ``...``
  indicates a number of leading singleton dimensions equal to the number of
  ``channel_dims`` + 1.
- The data tensor has shape
  (``batch_size``, ``*channel_dims``, ``n_rows``, ``k``).
"""
from functools import partial
import jax
import jax.numpy as jnp
import numpy as np
from jax.experimental.sparse import BCOO
from typing import Any, Literal, Optional, Sequence, Tuple, Union

from torch import threshold

from .utils import Tensor, vmap_over_outer


TopKTensor = Any


def random_sparse(
    shape: Sequence[int],
    k: Optional[int] = None,
    *,
    key: jax.random.PRNGKey,
) -> Tensor:
    """
    Generate a batch of random sparse matrices in the top-k format.
    """
    if k is None: k = max(shape[-1] // 100, 1)
    batch_size, *channel_dims, n_rows, n_cols = shape
    ikey, dkey = jax.random.split(key)
    ikeys = jax.random.split(ikey, n_rows)
    indices = jnp.stack([
        jax.random.choice(i, a=n_cols, shape=(k, 1), replace=False)
        for i in ikeys
    ], axis=0)
    idx_unsqueeze = tuple([None] * (1 + len(channel_dims)) + [...])
    data = jax.random.normal(dkey, (batch_size, *channel_dims, n_rows, k))
    return BCOO((data, indices[idx_unsqueeze]), shape=shape).sum_duplicates()


def sparse_astype(
    tensor: Tensor,
    dtype: Any
) -> Tensor:
    """
    Set the data type of a sparse matrix.

    This function is probably unnecessary, but I'm missing a way to do this
    with the current JAX API.
    """
    if tensor.dtype == dtype:
        return tensor
    return BCOO(
        (tensor.data.astype(dtype), tensor.indices),
        shape=tensor.shape
    )


def spdiagmm(
    lhs: Union[Tensor, TopKTensor],
    rhs: Union[Tensor, TopKTensor],
    lhs_diag: bool = False
) -> TopKTensor:
    """
    Matrix multiplication of a top-k format sparse matrix with a diagonal
    matrix. Returns a sparse matrix in the top-k format.

    The diagonal matrix should be formatted as a vector or batch of vectors
    whose final dimension is equal to the matching inner dimension of the
    top-k sparse matrix. All other dimensions should be broadcastable.

    A diagonal matrix that is in matrix form can be converted into a vector by
    calling a ``diagonal`` function that is appropriately vmapped.
    """
    if lhs_diag:
        data = lhs[..., None] * rhs.data
        indices = rhs.indices
        shape = rhs.shape
    else:
        data = lhs.data * rhs[..., tuple(lhs.indices.squeeze())]
        indices = lhs.indices
        shape = lhs.shape
    return BCOO((data, indices), shape=shape)


def spspmm_full(
    lhs: TopKTensor,
    rhs: TopKTensor
) -> Tensor:
    """
    Matrix multiplication of a top-k format sparse matrix with another sparse
    matrix in the top-k format. Returns a full matrix.
    """
    contracting_dims = ((lhs.ndim - 1,), (rhs.ndim - 1,))
    batch_dims = (tuple(range(lhs.ndim - 2)), tuple(range(rhs.ndim - 2)))
    return jax.experimental.sparse.bcoo_dot_general(
        lhs, rhs, dimension_numbers=(contracting_dims, batch_dims)
    )


def trace_spspmm(
    lhs: TopKTensor,
    rhs: TopKTensor,
    threshold: float = 0.0,
    threshold_type: Literal['abs>', 'abs<' '>', '<'] = 'abs>',
    top_k: bool = False,
    top_k_reduction: Optional[Literal['mean']] = 'mean',
    fix_indices_over_channel_dims: bool = True,
) -> Tensor:
    """
    Trace the matrix multiplication of two top-k format sparse matrices to
    determine the indices of nonzero entries.

    The inputs can be boolean matrices, in which case the output contains the
    indices of True entries. The inputs can also be matrices with values, in
    which case the output contains the indices of entries that survive the
    thresholding operation.

    .. warning::
        This function is not compatible with JIT compilation.

    .. warning::
        If the input is batched or contains multiple channels, the ``top_k``
        option will return separate indices for each channel and each batch
        element. Ensure that ``top_k_reduction`` is set to ``'mean'`` to
        obtain a single index across all batch elements (and potentially
        channels, according to ``fix_indices_over_channel_dims``).

    Parameters
    ----------
    lhs : TopKTensor
        The left-hand side sparse matrix.
    rhs : TopKTensor
        The right-hand side sparse matrix.
    threshold : float (default: 0.0)
        The threshold value. Used only if the input matrices are matrices with
        values.
    threshold_type : one of 'abs>', 'abs<', '>', '<' (default: 'abs>')
        The type of thresholding operation to perform.
    top_k : bool (default: False)
        If True, then the threshold value must be an integer, and the
        thresholding operation will be replaced by selection of the top k
        entries.
    fix_indices_over_channel_dims : bool (default: True)
        If True, then the indices of nonzero entries that are returned will
        be fixed over all channel dimensions. If False, then the indices of
        nonzero entries that are returned are allowed to vary over all channel
        dimensions.
    """
    out = spspmm_full(lhs, rhs)
    data = out.data.squeeze(-1)
    if fix_indices_over_channel_dims:
        fixed_axes = tuple(range(lhs.ndim - 2))
    else:
        fixed_axes = (0,)
    if lhs.dtype == jnp.bool_:
        data = data.any(axis=fixed_axes)
        return jnp.stack(jnp.where(data), axis=-1)
    elif not top_k:
        if threshold_type == 'abs>':
            data = jnp.abs(data) > threshold
        elif threshold_type == 'abs<':
            data = jnp.abs(data) < threshold
        elif threshold_type == '>':
            data = data > threshold
        elif threshold_type == '<':
            data = data < threshold
        data = data.any(axis=fixed_axes)
        return jnp.stack(jnp.where(data), axis=-1)
    else:
        if top_k_reduction == 'mean':
            data = data.mean(axis=fixed_axes)
        if not isinstance(threshold, int):
            raise ValueError(
                'If topk is True, then the threshold value must be an integer.'
            )
        if threshold_type == 'abs>':
            descending = True
            data = jnp.abs(data)
        elif threshold_type == 'abs<':
            descending = False
            data = jnp.abs(data)
        elif threshold_type == '>':
            descending = True
        elif threshold_type == '<':
            descending = False
        return topk(data, k=threshold, axis=-1, descending=descending)[..., None]


def _ix(x, i): return x[i]


def topk(
    tensor: Tensor,
    k: int,
    *,
    axis: int = -1,
    descending: bool = True,
) -> Tensor:
    """
    Select the top k entries of a tensor and return the indices in a format
    compatible with the top-k sparse matrix format.
    """
    if descending:
        tensor = -tensor
    arr = np.array(tensor)
    slc = [slice(None)] * arr.ndim
    slc[axis] = slice(None, k)
    slc = tuple(slc)
    return np.argpartition(arr, k - 1, axis=axis)[slc]


def as_topk(
    tensor: Tensor,
    k: int,
    *,
    descending: bool = True,
) -> TopKTensor:
    #TODO: allow user to specify axis (?)
    indices = topk(tensor, k, axis=-1, descending=descending)
    #axc = axis_complement(tensor.ndim, axis)
    data_fn = vmap_over_outer(_ix, 1)
    data = data_fn((tensor, indices))
    return BCOO((data, indices[..., None]), shape=tensor.shape)


# We temporarily need this obvious placeholder for importing the module.
spspmm = lambda lhs, rhs: 0


def random_sparse_batchfinal(key, shape, density=0.1):
    """
    Generate a random sparse matrix in batch-final COO format.
    """
    n = jnp.prod(jnp.array(shape))
    nse = int(density * n)
    k1, k2 = jax.random.split(key)
    indices = jax.random.choice(k1, a=n, shape=(nse,), replace=False)
    indices = jnp.stack(jnp.unravel_index(indices, shape), axis=-1)
    data = jax.random.normal(k2, (nse,))
    return BCOO((data, indices), shape=shape).sum_duplicates()


def to_batch_batchfinal(matrices: Sequence[Tensor]) -> Tensor:
    """
    Convert a sequence of sparse matrices to a batch of matrices using the
    batch-final, common-index COO format.

    .. note::
        This function is not intended to be compatible with JIT compilation.
    """
    batch_size = len(matrices)
    shape = sum([m.data.shape[0] for m in matrices])
    remaining_shape = matrices[0].data.shape[1:]
    indices = jnp.concatenate([m.indices for m in matrices], axis=0)
    data = jnp.zeros((shape, *remaining_shape, batch_size))
    start = 0
    for i, matrix in enumerate(matrices):
        end = start + matrix.data.shape[0]
        data = data.at[start:end, ..., i].set(matrix.data)
        start = end
    return BCOO(
        (data, indices),
        shape=(*matrices[0].shape, batch_size)
    ).sum_duplicates()


def _get_dense_dim_mm_batchfinal(lhs, rhs):
    """
    Get the dense dimension of the matrix multiplication.
    """
    lhs_dims = lhs.data.shape[1:]
    rhs_dims = rhs.data.shape[1:]
    # we don't check for broadcastability here
    return [max(l, r) for l, r in zip(lhs_dims, rhs_dims)]


def spspmm_batchfinal(lhs, rhs, inner_dims=(0, 0), outer_dims=(1, 1)):
    """
    Sparse-sparse matrix multiplication with vectorisation over trailing dense
    dimensions.

    .. note::
        This function is not recommended for use. It is maintained in case
        it is useful in the future. It is strongly recommended that you use
        the top-k format instead.
    """
    # lhs_shape = lhs.shape
    # rhs_shape = rhs.shape
    # lhs_dims = list(lhs_shape[:-lhs.n_dense])
    # rhs_dims = list(rhs_shape[:-lhs.n_dense])
    # lhs_dims[outer_dims[0]] = lhs_dims[inner_dims[0]] = None
    # rhs_dims[outer_dims[0]] = rhs_dims[inner_dims[0]] = None

    # only support 2D sparse for now
    assert lhs.n_sparse == rhs.n_sparse == 2
    dense_dim_out = _get_dense_dim_mm_batchfinal(lhs, rhs)
    out_shape = (
        lhs.shape[outer_dims[0]],
        rhs.shape[outer_dims[1]],
        *dense_dim_out
    )

    out_nse = lhs.nse * rhs.nse # memory use scales as product of NSEs
    lhs_data = lhs.data[None, ...]
    rhs_data = rhs.data[:, None, ...]

    lhs_contract_dim, rhs_contract_dim = inner_dims
    lhs_contract_idx = lhs.indices[:, lhs_contract_dim][None, :]
    rhs_contract_idx = rhs.indices[:, rhs_contract_dim][:, None]
    out_nonzero = (lhs_contract_idx == rhs_contract_idx)
    extra_idx = [None] * len(dense_dim_out)
    out_nonzero = out_nonzero[tuple([...] + extra_idx)]
    out_data = jnp.where(out_nonzero, lhs_data * rhs_data, 0.)

    lhs_indices = jnp.ones_like(lhs.indices).at[:, -2].set(
        lhs.indices[:, outer_dims[0]])
    rhs_indices = jnp.ones_like(rhs.indices).at[:, -1].set(
        rhs.indices[:, outer_dims[1]])
    out_indices = (lhs_indices[None, ...] * rhs_indices[:, None, ...])

    out_indices = out_indices.reshape(out_nse, -1)
    out_data = out_data.reshape(out_nse, *dense_dim_out)
    return BCOO((out_data, out_indices), shape=out_shape)


# def spspmm(lhs, rhs, inner_dims=(1, 1), batch_dims=(0, 0)):
#     contracting_dims = ((inner_dims[0],), (inner_dims[1],))
#     batch_dims = ((batch_dims[0],), (batch_dims[1],))
#     dimension_numbers = (contracting_dims, batch_dims)
#     return jax.experimental.sparse.bcoo_dot_general(
#         lhs, rhs, dimension_numbers=dimension_numbers
#     )
#     print(dimension_numbers)
#     fwd = jax.vmap(
#         partial(jax.experimental.sparse.bcoo_dot_general,
#                 dimension_numbers=dimension_numbers),
#         in_axes=batch_dims
#     )
#     return fwd(lhs, rhs)


def _promote_nnz_dim(values):
    return jnp.transpose(values, list(range(values.ndim))[::-1])
    # slightly faster but not as easy to implement
    #return jnp.transpose(values, (*list(range(values.ndim))[1:], 0))


def _demote_nnz_dim(values):
    return _promote_nnz_dim(values)
    #return jnp.transpose(values, (-1, *list(range(values.dim()))[:-1]))