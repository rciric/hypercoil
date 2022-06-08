# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Vertical compression layer.
"""
import torch
from torch import nn
from torch.nn import init, Parameter
from ..init.mpbl import BipartiteLatticeInit
from ..functional.matrix import delete_diagonal


class VerticalCompression(nn.Module):
    r"""
    Compress a graph by fusing vertices. For an adjacency matrix A, this
    layer applies the transform
    :math:`\left(C_{row} A\right) \times C_{col}^{\intercal}` so that an
    :math:`H_{in} \times W_{in}` matrix is mapped to an
    :math:`H_{out} \times W_{out}` matrix.
    """
    __constants__ = ['in_features', 'out_features']

    def __init__(self, in_features, out_features, init, renormalise=True,
                 fold_channels=True, learnable=True, device=None, dtype=None,
                 forward_operation='compress'):
        super(VerticalCompression, self).__init__()
        factory_kwargs = {'device': device, 'dtype': dtype}
        self.factory_kwargs = factory_kwargs
        self.in_features = in_features
        self.out_features = out_features
        self.initialised = False
        try:
            if isinstance(init[0], BipartiteLatticeInit):
                pass
        except TypeError:
            init = [init]
        self._out_channels = [(i.n_lattices * i.channel_multiplier)
                              for i in init]
        self.out_channels = sum(self._out_channels)
        self.C = Parameter(torch.empty(
            (self.out_channels, self.out_features, self.in_features),
            **factory_kwargs))
        self.mask = Parameter(torch.empty(
            (self.out_channels, self.out_features, self.in_features),
            dtype=torch.bool, device=device
        ), requires_grad=False)
        self.sign = Parameter(torch.empty(
            (self.out_channels, 1, 1), **factory_kwargs
        ), requires_grad=False)
        if not learnable:
            self.C.requires_grad = False
        self.init = init
        self.renormalise = renormalise
        self.fold_channels = fold_channels
        self.forward_operation = forward_operation
        self.reset_parameters()

    def reset_parameters(self):
        try:
            factory_kwargs = self.factory_kwargs
            start = 0
            for init, n_ch in zip(self.init, self._out_channels):
                end = start + n_ch
                tsr, msk = (
                    torch.empty((n_ch, self.out_features, self.in_features),
                                **factory_kwargs),
                    torch.empty((n_ch, self.out_features, self.in_features),
                                dtype=torch.bool,
                                device=factory_kwargs['device'])
                )
                init(tsr, msk)
                with torch.no_grad():
                    self.C[start:end] = tsr
                    self.mask[start:end] = msk
                    self.sign[start:end] = init.sign_vector()
                start = end
            self.initialised = True
        except AttributeError:
            return

        self.sparsity = (torch.sum(self.mask) /
                         torch.numel(self.mask)).item()

    def extra_repr(self):
        if self.initialised:
            s = ('compression=({}, {}), out_channels={}, sparsity={:.4}'
                ).format(self.in_features, self.out_features,
                         self.out_channels, self.sparsity)
        else:
            s = ('uninitialised=True, compression=({}, {}), out_channels={}'
                ).format(self.in_features, self.out_features,
                         self.out_channels)
        return s

    def reconstruct(self, compressed):
        if not self.initialised:
            raise ValueError(
                'Vertical compression module has not been initialised. '
                'Ensure that potentials have been configured for all '
                'initialisers passed to the module.'
            )
        return vertical_compression(
            input=compressed,
            row_compressor=(self.mask * self.C).transpose(-1, -2),
            renormalise=self.renormalise,
            remove_diagonal=True,
            fold_channels=self.fold_channels,
            sign=self.sign
        )

    def compress(self, input):
        if not self.initialised:
            raise ValueError(
                'Vertical compression module has not been initialised. '
                'Ensure that potentials have been configured for all '
                'initialisers passed to the module.'
            )
        return vertical_compression(
            input=input,
            row_compressor=(self.mask * self.C),
            renormalise=self.renormalise,
            remove_diagonal=True,
            fold_channels=self.fold_channels,
            sign=self.sign
        )

    def forward(self, input):
        if self.forward_operation == 'compress':
            return self.compress(input=input)
        elif self.forward_operation == 'reconstruct':
            return self.reconstruct(compressed=input)


##TODO: move to `functional` at some point.
def vertical_compression(input, row_compressor, col_compressor=None,
                         renormalise=True, remove_diagonal=False,
                         fold_channels=True, sign=None):
    r"""
    Vertically compress a matrix or matrix stack of dimensions
    :math:`H_{in} \times W_{in} \rightarrow H_{out} \times W_{out}`.

    Parameters
    ----------
    input: Tensor
        Tensor to be compressed. This can be either a matrix of dimension
        :math:`H_{in} \times W_{in}` or a stack of such matrices, for
        instance of dimension :math:`N \times C \times H_{in} \times W_{in}`.
    row_compressor: Tensor
        Compressor for the rows of the input tensor. This should be a matrix
        of dimension :math:`H_{out} \times H_{in}`.
    col_compressor: Tensor or None
        Compressor for the columns of the input tensor. This should be a
        matrix of dimension :math:`W_{out} \times W_{in}`. If this is None,
        then symmetry is assumed: the column compressor and row compressor are the same.
    """
    if delete_diagonal:
        input = delete_diagonal(input)
    input = input.unsqueeze(-3)
    if col_compressor is None:
        col_compressor = row_compressor
    compressed = (row_compressor @ input) @ col_compressor.transpose(-2, -1)
    if remove_diagonal:
        compressed = delete_diagonal(compressed)
    if sign is not None:
        compressed = sign * compressed
    if renormalise:
        fac = (compressed.std((-1, -2), keepdim=True) /
               input.std((-1, -2), keepdim=True))
        compressed = compressed / fac
    if fold_channels:
        n = compressed.shape[0]
        h, w = compressed.shape[-2:]
        compressed = compressed.view(n, -1, h, w)
    return compressed