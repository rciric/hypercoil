# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
IIR filter initialisation
~~~~~~~~~~~~~~~~~~~~~~~~~
Tools for initialising parameters to match the transfer function of an IIR or
ideal filter.
"""
import torch
import math


class IIRFilterSpec(object):
    def __init__(self, Wn, N=1, ftype='butter', btype='bandpass',
                 fs=None, rp=0.1, rs=20, norm='phase', bound=3):
        N = _ensure_tensor(N)
        Wn = _ensure_tensor(Wn)
        if btype in ('bandpass', 'bandstop') and Wn.ndim < 2:
            Wn = Wn.view(-1, 2)
        self.N = N
        self.Wn = Wn
        self.ftype = ftype
        self.btype = btype
        self.fs = fs
        self.rp = rp
        self.rs = rs
        self.norm = norm
        self.bound = bound
        self.n_filters = len(self.N)

    def initialise_spectrum(self, worN, domain='atanh', ood='clip'):
        if self.ftype == 'butter':
            self.spectrum = butterworth_spectrum(
                N=self.N, Wn=self.Wn, btype=self.btype, worN=worN, fs=self.fs)
        if self.ftype == 'cheby1':
            self.spectrum = chebyshev1_spectrum(
                N=self.N, Wn=self.Wn, rp=self.rp, btype=self.btype,
                worN=worN, fs=self.fs)
        if self.ftype == 'cheby2':
            self.spectrum = chebyshev2_spectrum(
                N=self.N, Wn=self.Wn, rs=self.rs, btype=self.btype,
                worN=worN, fs=self.fs)
        if self.ftype == 'ellip':
            self.spectrum = elliptic_spectrum(
                N=self.N, Wn=self.Wn, rp=self.rp, rs=self.rs,
                btype=self.btype, worN=worN, fs=self.fs)
        if self.ftype == 'bessel':
            self.spectrum = bessel_spectrum(
                N=self.N, Wn=self.Wn, norm=self.norm,
                btype=self.btype, worN=worN, fs=self.fs)
        if self.ftype == 'ideal':
            self.spectrum = ideal_spectrum(
                Wn=self.Wn, btype=self.btype, worN=worN, fs=self.fs)
        self._transform_and_bound_spectrum(domain, ood)

    def _transform_and_bound_spectrum(self, domain, ood):
        ampl = torch.abs(self.spectrum)
        phase = torch.angle(self.spectrum)
        if domain == 'linear':
            return None
        elif domain == 'atanh':
            ampl = self._handle_ood(ampl, bound=1, ood=ood)
            ampl = torch.atanh(ampl)
            self._bound_and_recompose(ampl, phase, ood=ood)

    def _handle_ood(self, ampl, bound, ood):
        if ood == 'clip':
            ampl[ampl > bound] = bound
        elif ood == 'norm' and ampl.max(0) > bound:
            ampl /= (ampl.max(0) / bound)
        return ampl

    def _bound_and_recompose(self, ampl, phase, ood):
        ampl = self._handle_ood(ampl, self.bound, ood)
        self.spectrum = ampl * torch.exp(phase * 1j)

    def __repr__(self):
        s = (f'IIRFilterSpec(ftype={self.ftype}, n_filters={self.n_filters})')
        return s


def iirfilter_init_(tensor, filter_specs, domain='atanh', ood='clip'):
    """
    IIR filter-like transfer function initialisation.

    Initialise a tensor such that its values follow the transfer function of
    an IIR or ideal filter. For IIR filters, the transfer function is computed
    as a frequency response curve in scipy.

    Dimension
    ---------
    - tensor : :math:`(*, F, N)`
      F denotes the total number of filters to initialise from the provided
      specs, and N denotes the number of frequency bins.

    Parameters
    ----------
    tensor : Tensor
        Tensor to initialise in-place. The import will include only the real
        part (and will therefore be incorrect for most filters) if the provided
        tensor does not have a complex datatype. Note that even if the transfer
        function is strictly real, the gradient will almost certainly not be
        and it is therefore critical that this tensor allow complex values.
    filter_specs : list(IIRFilterSpec)
        A list of filter specifications implemented as `IIRFilterSpec` objects
        (`hypernova.init.IIRFilterSpec`).
    ood : 'clip' or 'norm' (default `clip`)
        Indicates how out-of-domain values should be handled at initialisation.
        `clip` indicates that out-of-domain values should be clipped to the
        closest allowed point and `norm` indicates that the entire spectrum
        (in-domain and out-of-domain values) should be re-scaled so that it
        fits in the domain bounds (not recommended).
    """
    rg = tensor.requires_grad
    tensor.requires_grad = False
    worN = tensor.size(-1)
    for fspec in filter_specs:
        fspec.initialise_spectrum(worN, domain, ood)
    spectra = torch.cat([fspec.spectrum for fspec in filter_specs])
    tensor[:] = spectra
    tensor.requires_grad = rg


def butterworth_spectrum(N, Wn, worN, btype='bandpass', fs=None):
    """
    Butterworth filter's transfer function obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified Butterworth transfer function.
    """
    from scipy.signal import butter
    filter_params, spectrum_params = {}, {}
    return iirfilter_spectrum(
        iirfilter=butter,
        N=N, Wn=Wn, worN=worN,
        btype=btype, fs=fs,
        filter_params=filter_params,
        spectrum_params=spectrum_params)


def chebyshev1_spectrum(N, Wn, worN, rp, btype='bandpass', fs=None):
    """
    Chebyshev I filter's transfer function obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    rp : float
        Pass-band ripple parameter.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified Chebyshev I transfer function.
    """
    from scipy.signal import cheby1
    filter_params = {
        'rp': rp
    }
    spectrum_params = {}
    return iirfilter_spectrum(
        iirfilter=cheby1,
        N=N, Wn=Wn, worN=worN,
        btype=btype, fs=fs,
        filter_params=filter_params,
        spectrum_params=spectrum_params)


def chebyshev2_spectrum(N, Wn, worN, rs, btype='bandpass', fs=None):
    """
    Chebyshev II filter's transfer function obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    rs : float
        Stop-band ripple parameter.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified Chebyshev II transfer function.
    """
    from scipy.signal import cheby2
    filter_params = {
        'rs': rs
    }
    spectrum_params = {}
    return iirfilter_spectrum(
        iirfilter=cheby2,
        N=N, Wn=Wn, worN=worN,
        btype=btype, fs=fs,
        filter_params=filter_params,
        spectrum_params=spectrum_params)


def elliptic_spectrum(N, Wn, worN, rp, rs, btype='bandpass', fs=None):
    """
    Elliptic filter's transfer function obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    rp : float
        Pass-band ripple parameter.
    rs : float
        Stop-band ripple parameter.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified elliptic transfer function.
    """
    from scipy.signal import ellip
    filter_params = {
        'rp': rp,
        'rs': rs
    }
    spectrum_params = {}
    return iirfilter_spectrum(
        iirfilter=ellip,
        N=N, Wn=Wn, worN=worN,
        btype=btype, fs=fs,
        filter_params=filter_params,
        spectrum_params=spectrum_params)


def bessel_spectrum(N, Wn, worN, norm='phase', btype='bandpass', fs=None):
    """
    Bessel-Thompson filter's transfer function obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    norm : 'phase', 'delay' or 'mag'
        Critical frequency normalisation. Consult the `scipy.signal.bessel`
        documentation for details.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified elliptic transfer function.
    """
    from scipy.signal import bessel
    filter_params = {
        'norm': norm
    }
    spectrum_params = {}
    return iirfilter_spectrum(
        iirfilter=bessel,
        N=N, Wn=Wn, worN=worN,
        btype=btype, fs=fs,
        filter_params=filter_params,
        spectrum_params=spectrum_params)


def ideal_spectrum(Wn, worN, btype='bandpass', fs=None):
    """
    Ideal filter transfer function.

    Dimension
    ---------
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.

    Returns
    -------
    out : Tensor
        The specified ideal transfer function.
    """
    Wn = _ensure_tensor(Wn)
    if btype in ('bandpass', 'bandstop') and Wn.ndim < 2:
        Wn = Wn.view(-1, 2)
    if fs is not None:
        Wn = 2 * Wn / fs
    frequencies = torch.linspace(0, 1, worN)
    if btype == 'lowpass':
        response = frequencies < Wn.view(-1, 1)
    elif btype == 'highpass':
        response = frequencies > Wn.view(-1, 1)
    elif btype == 'bandpass':
        response_hp = frequencies > Wn[:, 0].view(-1, 1)
        response_lp = frequencies < Wn[:, 1].view(-1, 1)
        response = response_hp * response_lp
    elif btype == 'bandstop':
        response_hp = frequencies < Wn[:, 0].view(-1, 1)
        response_lp = frequencies > Wn[:, 1].view(-1, 1)
        response = response_hp + response_lp
    return response.float()


def iirfilter_spectrum(iirfilter, N, Wn, worN, btype='bandpass', fs=None,
                       filter_params=None, spectrum_params=None):
    """
    Transfer function for an IIR filter, obtained via import from scipy.

    Dimension
    ---------
    - N : :math:`(F)`
      F denotes the total number of filter to initialise.
    - Wn : :math:`(F, 2)` for bandpass or bandstop or :math:`(F)` otherwise

    Parameters
    ----------
    iirfilter : callable
        `scipy.signal` filter function corresponding to the filter to be
        estimated, for instance `butter` for a Butterworth filter.
    N : int or Tensor
        Filter order. If this is a tensor, then a separate filter will be
        created for each entry in the tensor. Wn must be shaped to match.
    Wn : float or tuple(float, float) or Tensor
        Critical or cutoff frequency. If this is a band-pass filter, then this
        should be a tuple, with the first entry specifying the high-pass cutoff
        and the second entry specifying the low-pass frequency. This should be
        specified relative to the Nyquist frequency if `fs` is not provided,
        and should be in the same units as `fs` if it is provided. To create
        multiple filters, specify a tensor containing the critical frequencies
        for each filter in a single row.
    worN : int
        Number of frequency bins to include in the computed spectrum.
    btype : 'lowpass', 'highpass', or 'bandpass' (default 'bandpass')
        Filter type to emulate: low-pass, high-pass, or band-pass. The
        interpretation of the critical frequency changes depending on the
        filter type.
    fs : float or None (default None)
        Sampling frequency.
    filter_params : dict
        Additional parameters to pass to the `iirfilter` callable other than
        those passed directly to this function (for instance, pass- and
        stop-band ripples).
    spectrum_params : dict
        Additional parameters to pass to the `freqz` function that computes
        the frequency response spectrum other than those passed directly to
        this function.

    Returns
    -------
    out : Tensor
        The specified filter's transfer function.
    """
    import numpy as np
    from scipy.signal import freqz
    N = _ensure_ndarray(N).astype(int)
    Wn = _ensure_ndarray(Wn)
    if btype in ('bandpass', 'bandstop') and Wn.ndim < 2:
        Wn = Wn.reshape(-1, 2)
    vals = [
        iirfilter(N=n, Wn=wn, btype=btype, fs=fs, **filter_params)
        for n, wn in zip(N, Wn)
    ]
    fs = fs or 2 * math.pi
    vals = [
        freqz(b, a, worN=worN, fs=fs, include_nyquist=True, **spectrum_params)
        for b, a in vals
    ]
    vals = np.stack([v for _, v in vals])
    return _import_complex_numpy(vals)


def _ensure_ndarray(obj):
    """
    Ensure that the object is an iterable ndarray with dimension greater than
    or equal to 1. Another function we'd do well to get rid of in the future.
    """
    import numpy as np
    try:
        i = iter(obj)
        return np.array(obj)
    except TypeError:
        return np.array([obj])


def _ensure_tensor(obj):
    """
    Ensure that the object is an iterable tensor with dimension greater than
    or equal to 1. Another function we'd do well to get rid of in the future.
    """
    try:
        i = iter(obj)
        return torch.Tensor(obj)
    except TypeError:
        return torch.Tensor([obj])


def _import_complex_numpy(array):
    """
    Hacky import of complex-valued array from numpy into torch. Hopefully this
    can go away in the future. Simply calling torch.Tensor casts the input to
    real, and we would otherwise have to specify a particular precision for the
    import which might not match the precision desired.
    """
    real = torch.Tensor(array.real)
    imag = torch.Tensor(array.imag)
    val = torch.stack([real, imag], -1)
    return torch.view_as_complex(val)
