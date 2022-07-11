# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Brain surface plots with `surfplot`.
These currently require a custom patch of `surfplot`.
"""
import torch
import surfplot
import numpy as np
import nibabel as nb
import matplotlib
import matplotlib.pyplot as plt
import templateflow.api as tflow
from nilearn import plotting
from hypercoil.engine import Sentry
from hypercoil.functional.cmass import cmass_coor
from hypercoil.functional.sphere import spherical_geodesic
from hypercoil.neuro.const import fsLR
from hypercoil.viz.surfutils import _CMapFromSurfMixin


POLE_DECODER = {
    'cortex_L': np.array([
        'medial', 'anterior', 'dorsal',
        'lateral', 'posterior', 'ventral'
    ]),
    'cortex_R': np.array([
        'lateral', 'anterior', 'dorsal',
        'medial', 'posterior', 'ventral'
    ]),
}

POLES = torch.tensor([
    [100., 0., 0.],
    [0., 100., 0.],
    [0., 0., 100.],
    [-100., 0., 0.],
    [0., -100., 0.],
    [0., 0., -100.],
])


VIEWS = {
    'dorsal' : {
        'views' : 'dorsal',
        'size' : (250, 300),
        'zoom' : 3
    },
    'ventral' : {
        'views' : 'ventral',
        'size' : (250, 300),
        'zoom' : 3,
        'flip' : True
    },
    'posterior' : {
        'views' : 'posterior',
        'size' : (300, 300),
        'zoom' : 3
    },
    'anterior' : {
        'views' : 'anterior',
        'size' : (300, 300),
        'zoom' : 3,
        'flip' : True
    },
    'medial' : {
        'views' : 'medial',
        'size' : (900, 300),
        'zoom' : 1.8,
    },
    'lateral' : {
        'views' : 'lateral',
        'size' : (900, 300),
        'zoom' : 1.8,
    },
}


class fsLRSurfacePlot(Sentry):
    def __init__(self, atlas):
        super().__init__()
        self.module = atlas
        self.atlas = atlas.atlas
        coor_query = fsLR().TFLOW_COOR_QUERY
        coor_query.update(suffix='veryinflated')
        self.lh, self.rh = (
            tflow.get(**coor_query, hemi='L'),
            tflow.get(**coor_query, hemi='R')
        )
        self.dim_lh = nb.load(self.lh).darrays[0].dims[0]
        self.dim_rh = nb.load(self.rh).darrays[0].dims[0]
        self.dim = self.dim_lh + self.dim_rh

        self.data_mask = {
            'cortex_L' : torch.ones_like(self.atlas.mask),
            'cortex_R' : torch.ones_like(self.atlas.mask),
            'all' : torch.ones_like(self.atlas.mask)
        }
        self.cmap_mask = {
            'cortex_L' : self.atlas.compartments['cortex_L'].clone(),
            'cortex_R' : self.atlas.compartments['cortex_R'].clone(),
            'all' : self.atlas.mask.clone()
        }
        self.data_mask['cortex_L'][self.dim_lh:] = False
        self.data_mask['cortex_R'][:self.dim_lh] = False
        self.data_mask['cortex_R'][self.dim:] = False
        self.data_mask['all'][self.dim:] = False
        self.cmap_mask['cortex_L'][self.dim_lh:] = False
        self.cmap_mask['cortex_R'][:self.dim_lh] = False
        self.cmap_mask['cortex_R'][self.dim:] = False
        self.cmap_mask['all'][self.dim:] = False
        self.cortical_mask = (
            self.atlas.compartments['cortex_L'] |
            self.atlas.compartments['cortex_R']
        )
        self.coor_all = self.atlas.coors[self.cortical_mask[self.atlas.mask]]

    def drop_null(self, null=0):
        subset = (self.data != null)
        return self.data[subset], self.coor_all[subset]


class fsLRAtlasParcels(
    _CMapFromSurfMixin,
    fsLRSurfacePlot
):
    def __call__(self, cmap=None, views=('lateral', 'medial'), contours=False,
                 one_fig=False, save=None, figsize=None, scores=None,
                 **params):
        if figsize is None:
            base_dim = 20
            if one_fig:
                figsize = (base_dim * len(views), base_dim * 2)
            else:
                figsize = (base_dim, base_dim * 2)
        if save is not None:
            matplotlib.use('agg')
        data = torch.zeros_like(self.atlas.mask, dtype=torch.long)
        for compartment in ('cortex_L', 'cortex_R'):
            mask = self.atlas.compartments[compartment]
            labels = self.module.weight[compartment].argmax(0).detach()
            compartment_data = (
                self.atlas.decoder[compartment][labels]
            )
            compartment_data[self.module.weight[compartment].sum(0) == 0] = 0
            data[mask] = compartment_data
        zero_mask = (data == 0)
        if scores is not None:
            scores = torch.tensor(scores, device='cpu')
            data = scores[data - 1] #TODO: use uniques instead
            data[zero_mask] = 0
        self.data = data[self.cmap_mask['all']].cpu()
        self.data_lh = data[self.data_mask['cortex_L']].cpu().numpy()
        self.data_rh = data[self.data_mask['cortex_R']].cpu().numpy()
        labels = self.atlas.decoder['_all']
        if cmap is not None:
            try:
                cmap = self._select_cmap(cmap=cmap, labels=labels)
                vmin = 1
                vmax = labels.max()
            except FileNotFoundError:
                vlim = scores.abs().max().numpy()
                vmin = -vlim
                vmax = vlim
        else:
            cmap = 'gist_ncar'
        self.data = data[self.data_mask['all']].cpu().numpy()

        lh, rh = [], []
        if one_fig:
            fig, ax = plt.subplots(
                2, len(views),
                subplot_kw={'projection': '3d'},
                figsize=figsize
            )
        for i, view in enumerate(views):
            if one_fig:
                ax_lh = ax[0][i]
                ax_rh = ax[1][i]
            else:
                fig, ax = plt.subplots(
                    1, 2,
                    subplot_kw={'projection': '3d'},
                    figsize=figsize
                )
                ax_lh = ax[0]
                ax_rh = ax[1]
            lh_cur = plotting.plot_surf_roi(
                surf_mesh=str(self.lh),
                roi_map=self.data_lh,
                hemi='left',
                view=view,
                axes=ax_lh,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                **params
            )
            rh_cur = plotting.plot_surf_roi(
                surf_mesh=str(self.rh),
                roi_map=self.data_rh,
                hemi='right',
                view=view,
                axes=ax_rh,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                **params
            )
            if contours:
                lh_levels = np.unique(self.data_lh)
                rh_levels = np.unique(self.data_rh)
                plotting.plot_surf_contours(
                    surf_mesh=str(self.lh),
                    roi_map=self.data_lh,
                    axes=ax_lh,
                    levels=lh_levels,
                    labels=[None for _ in lh_levels],
                    colors=['k' for _ in lh_levels],
                )
                plotting.plot_surf_contours(
                    surf_mesh=str(self.rh),
                    roi_map=self.data_rh,
                    axes=ax_rh,
                    levels=rh_levels,
                    labels=[None for _ in rh_levels],
                    colors=['#00000033' for _ in rh_levels],
                )
            if not one_fig:
                lh += [lh_cur]
                rh += [rh_cur]
            if save is not None and not one_fig:
                plt.savefig(f'{save}_view-{view}.png',
                            bbox_inches='tight')
                plt.close('all')
        if save is not None and one_fig:
            plt.savefig(f'{save}.png',
                        bbox_inches='tight')
        elif one_fig:
            return fig, cmap
        else:
            return lh, rh, cmap


class fsLRAtlasMaps(fsLRSurfacePlot):
    def plot_nodemaps(self, figs, max_per_batch=21, scale=(2, 2),
                      nodes_per_row=3, start_batch=0, stop_batch=None,
                      save=None):
        # This ridiculous-looking hack is necessary to ensure the first
        # figure is saved with the correct proportions.
        plt.figure()
        plt.close('all')

        n_figs = len(figs)
        n_batches = int(np.ceil(n_figs / max_per_batch))

        figs_plotted = 0
        figs_remaining = n_figs
        batch_index = start_batch
        if stop_batch is None:
            stop_batch = n_batches
        stop_batch = min(stop_batch, n_batches)

        while batch_index < stop_batch:
            start_fig = batch_index * max_per_batch
            figs_remaining = n_figs - start_fig
            figs_per_batch = min(max_per_batch, figs_remaining)
            stop_fig = start_fig + figs_per_batch

            n_rows = int(np.ceil(figs_per_batch / nodes_per_row))
            figsize = (6 * nodes_per_row, 3 * n_rows)


            fig, ax = plt.subplots(
                n_rows,
                nodes_per_row,
                figsize=figsize,
                num=1,
                clear=True
            )
            batch_figs = figs[start_fig:stop_fig]
            for index, (name, f) in enumerate(batch_figs):
                i = index // nodes_per_row
                j = index % nodes_per_row
                f._check_offscreen()
                x = f.to_numpy(transparent_bg=True, scale=(scale))
                ax[i, j].imshow(x)
                ax[i, j].set_xticks([])
                ax[i, j].set_yticks([])
                ax[i, j].set_title(f'Node {name}')

            if save:
                plt.tight_layout()
                plt.savefig(f'{save}_batch-{batch_index}.png',
                            dpi=300,
                            bbox_inches='tight')
                fig.clear()
                plt.close('all')
            else:
                fig.show()
            batch_index += 1

    def __call__(self, cmap='Blues', color_range=(0, 1),
                 max_per_batch=21, stop_batch=None, save=None):
        offscreen = False
        if save is not None:
            matplotlib.use('agg')
            offscreen = True
        figs = [None for _ in range(self.atlas.decoder['_all'].max() + 1)]
        for compartment in ('cortex_L', 'cortex_R'):
            map = self.module.weight[compartment]
            decoder = self.atlas.decoder[compartment]
            compartment_mask = self.atlas.compartments[compartment]
            coor = self.atlas.coors[compartment_mask[self.atlas.mask]].t()
            cmasses = cmass_coor(map, coor, radius=100)
            closest_poles = spherical_geodesic(
                cmasses.t(),
                POLES.to(device=cmasses.device, dtype=cmasses.dtype)
            ).argsort(-1)[:, :3].cpu()
            closest_poles = POLE_DECODER[compartment][closest_poles.numpy()]
            if compartment == 'cortex_L':
                surf_lh = self.lh
                surf_rh = None
            elif compartment == 'cortex_R':
                surf_lh = None
                surf_rh = self.rh
            for node, views, name in zip(map, closest_poles, decoder):
                data = torch.zeros_like(
                    self.atlas.mask,
                    dtype=map.dtype
                )
                data[compartment_mask] = node.detach()
                data = data[self.data_mask[compartment]].cpu().numpy()
                p = surfplot.Plot(
                    surf_lh=surf_lh,
                    surf_rh=surf_rh,
                    brightness=1,
                    views=views.tolist(),
                    zoom=1.25,
                    size=(400, 200)
                )
                p.offscreen = offscreen
                p.add_layer(
                    data[:self.dim],
                    cmap=cmap,
                    cbar=None,
                    color_range=color_range
                )
                figs[name] = p.render()
        figs = [(i, f) for i, f in enumerate(figs) if f is not None]

        n_figs = len(figs)
        batches_per_run = 5
        if stop_batch is None:
            total_batches = int(np.ceil(n_figs / max_per_batch))
        else:
            total_batches = stop_batch
        self.plot_nodemaps(figs=figs, save=save,
                           stop_batch=total_batches)
