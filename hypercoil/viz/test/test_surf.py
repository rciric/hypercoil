# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Unit tests for surfplot-based visualisations
"""
import pytest
# import torch
# from templateflow import api as tflow
# from pkg_resources import resource_filename as pkgrf
# from hypercoil.init.atlas import CortexSubcortexCIfTIAtlas
# from hypercoil.nn import AtlasLinear
# from hypercoil.viz.surf import (
#     fsLRAtlasParcels,
#     fsLRAtlasMaps
# )
import pyvista as pv
import numpy as np
from matplotlib.colors import ListedColormap

from pkg_resources import resource_filename as pkgrf

from hypercoil.viz.surf import (
    CortexTriSurface,
    make_cmap,
)


class TestSurfaceVisualisations:
    @pytest.mark.ci_unsupported
    def test_surf(self):
        surf = CortexTriSurface.from_tflow(
            load_mask=True,
            projections=('veryinflated', 'inflated', 'sphere')
        )
        parcellation = '/Users/rastkociric/Downloads/desc-schaefer_res-0400_atlas.nii'
        parcellation = '/Users/rastkociric/Downloads/glasser.nii'
        parcellation = '/Users/rastkociric/Downloads/gordon.nii'
        parcellation = pkgrf(
            'hypercoil',
            'viz/resources/nullexample.nii'
        )
        surf.add_cifti_dataset(
            name='parcellation',
            cifti=parcellation,
            is_masked=True,
            apply_mask=False,
            null_value=None,
        )

        cmap = pkgrf(
            'hypercoil',
            'viz/resources/cmap_network.nii'
        )
        surf.add_cifti_dataset(
            name='cmap',
            cifti=cmap,
            is_masked=True,
            apply_mask=False,
            null_value=0.
        )

        (cmap_left, clim_left), (cmap_right, clim_right) = make_cmap(
            surf, 'cmap', 'parcellation')

        pl = pv.Plotter()
        surf.left.project('veryinflated')
        pl.add_mesh(
            surf.left,
            opacity=1.0,
            show_edges=False,
            scalars='parcellation',
            cmap=cmap_left,
            clim=clim_left,
            below_color='black',)
        pl.add_mesh(
            surf.left.contour(
                isosurfaces=range(int(max(surf.left.point_data['parcellation'])))
            ),
            color="black",
            line_width=5)
        pl.show(cpos="yz")

        pl = pv.Plotter()
        surf.right.project('veryinflated')
        pl.add_mesh(
            surf.right,
            opacity=1.0,
            show_edges=False,
            scalars='parcellation',
            cmap=cmap_right,
            clim=clim_right,
            below_color='black',)
        pl.add_mesh(
            surf.right.contour(
                isosurfaces=range(int(max(surf.right.point_data['parcellation'])))
            ),
            color="black",
            line_width=5)
        pl.show(cpos="yz")

    # @pytest.fixture(autouse=True)
    # def setup_class(self):
    #     ref_pointer = pkgrf(
    #         'hypercoil',
    #         'viz/resources/nullexample.nii'
    #     )
    #     self.atlas = CortexSubcortexCIfTIAtlas(
    #         ref_pointer=ref_pointer,
    #         mask_L=tflow.get(
    #             template='fsLR',
    #             hemi='L',
    #             desc='nomedialwall',
    #             density='32k'),
    #         mask_R=tflow.get(
    #             template='fsLR',
    #             hemi='R',
    #             desc='nomedialwall',
    #             density='32k'),
    #         clear_cache=False,
    #         dtype=torch.float
    #     )
    #     self.lin = AtlasLinear(self.atlas)
    #     self.modal_cmap = pkgrf(
    #         'hypercoil',
    #         'viz/resources/cmap_modal.nii'
    #     )
    #     self.network_cmap = pkgrf(
    #         'hypercoil',
    #         'viz/resources/cmap_network.nii'
    #     )

    # @pytest.mark.ci_unsupported
    # def test_parcellation_plotter(self):
    #     all_views = (
    #         'dorsal', 'ventral',
    #         'posterior', 'anterior',
    #         'medial', 'lateral'
    #     )
    #     results = pkgrf(
    #         'hypercoil',
    #         'results/'
    #     )
    #     plotter = fsLRAtlasParcels(self.lin)
    #     plotter(
    #         cmap=self.modal_cmap,
    #         views=all_views,
    #         save=f'{results}/parcellation_cmap-modal'
    #     )
    #     plotter(
    #         cmap=self.network_cmap,
    #         views=all_views,
    #         save=f'{results}/parcellation_cmap-network'
    #     )
    #     plotter(
    #         cmap='RdBu_r',
    #         views=all_views,
    #         scores=torch.randn(400),
    #         save=f'{results}/parcellation_desc-randomstats'
    #     )

    # @pytest.mark.ci_unsupported
    # def test_map_plotter(self):
    #     results = pkgrf(
    #         'hypercoil',
    #         'results/'
    #     )
    #     plotter = fsLRAtlasMaps(self.lin)
    #     plotter(save=f'{results}/parcellation_maps', stop_batch=1)
    #     plotter(save=f'{results}/parcellation_maps_selected',
    #             select_nodes=[1, 3, 9, 221, 235])
