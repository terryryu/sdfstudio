# Copyright 2022 The Nerfstudio Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Code for sampling pixels.
"""

import random
from typing import Dict

import torch

from nerfstudio.utils.images import BasicImages


def collate_image_dataset_batch(batch: Dict, num_rays_per_batch: int, keep_full_image: bool = False):
    """
    Operates on a batch of images and samples pixels to use for generating rays.
    Returns a collated batch which is input to the Graph.
    It will sample only within the valid 'mask' if it's specified.

    Args:
        batch: batch of images to sample from
        num_rays_per_batch: number of rays to sample per batch
        keep_full_image: whether or not to include a reference to the full image in returned batch
    """
    device = batch["image"].device
    num_images, image_height, image_width, _ = batch["image"].shape

    # only sample within the mask, if the mask is in the batch
    if "mask" in batch:
        nonzero_indices = torch.nonzero(batch["mask"][..., 0], as_tuple=False)
        chosen_indices = random.sample(range(len(nonzero_indices)), k=num_rays_per_batch)
        indices = nonzero_indices[chosen_indices]
    else:
        indices = torch.floor(
            torch.rand((num_rays_per_batch, 3), device=device)
            * torch.tensor([num_images, image_height, image_width], device=device)
        ).long()

    c, y, x = (i.flatten() for i in torch.split(indices, 1, dim=-1))

    collated_batch = {
        key: value[c, y, x]
        for key, value in batch.items()
        if key not in ("image_idx", "src_imgs", "src_idxs") and value is not None
    }

    assert collated_batch["image"].shape == (num_rays_per_batch, 3), collated_batch["image"].shape

    # Needed to correct the random indices to their actual camera idx locations.
    indices[:, 0] = batch["image_idx"][c]
    collated_batch["indices"] = indices  # with the abs camera indices

    if keep_full_image:
        collated_batch["full_image"] = batch["image"]

    return collated_batch


def collate_image_dataset_batch_list(batch: Dict, num_rays_per_batch: int, keep_full_image: bool = False):
    """
    Does the same as collate_image_dataset_batch, except it will operate over a list of images / masks inside
    a list.

    We will use this with the intent of DEPRECIATING it as soon as we find a viable alternative.
    The intention will be to replace this with a more efficient implementation that doesn't require a for loop, but
    since pytorch's ragged tensors are still in beta (this would allow for some vectorization), this will do

    Args:
        batch: batch of images to sample from
        num_rays_per_batch: number of rays to sample per batch
        keep_full_image: whether or not to include a reference to the full image in returned batch
    """

    device = batch["image"][0].device
    num_images = len(batch["image"])

    # only sample within the mask, if the mask is in the batch
    all_indices = []
    all_images = []
    all_skys = []

    if "mask" in batch:
        num_rays_in_batch = num_rays_per_batch // num_images
        for i in range(num_images):
            if i == num_images - 1:
                num_rays_in_batch = num_rays_per_batch - (num_images - 1) * num_rays_in_batch
            # nonzero_indices = torch.nonzero(batch["mask"][i][..., 0], as_tuple=False)
            nonzero_indices = batch["mask"][i]

            chosen_indices = random.sample(range(len(nonzero_indices)), k=num_rays_in_batch)
            indices = nonzero_indices[chosen_indices]
            indices = torch.cat([torch.full((num_rays_in_batch, 1), i, device=device), indices], dim=-1)
            all_indices.append(indices)
            all_images.append(batch["image"][i][indices[:, 1], indices[:, 2]])
            all_skys.append(batch["sky"][i][indices[:, 1], indices[:, 2]])

    else:
        num_rays_in_batch = num_rays_per_batch // num_images
        for i in range(num_images):
            image_height, image_width, _ = batch["image"][i].shape
            if i == num_images - 1:
                num_rays_in_batch = num_rays_per_batch - (num_images - 1) * num_rays_in_batch
            indices = torch.floor(
                torch.rand((num_rays_in_batch, 3), device=device)
                * torch.tensor([1, image_height, image_width], device=device)
            ).long()
            indices[:, 0] = i
            all_indices.append(indices)
            all_images.append(batch["image"][i][indices[:, 1], indices[:, 2]])
            all_skys.append(batch["sky"][i][indices[:, 1], indices[:, 2]])

    indices = torch.cat(all_indices, dim=0)

    c, y, x = (i.flatten() for i in torch.split(indices, 1, dim=-1))
    collated_batch = {
        key: value[c, y, x]
        for key, value in batch.items()
        if key != "image_idx" and key != "image" and key != "mask" and key != "sky" and value is not None
    }

    collated_batch["image"] = torch.cat(all_images, dim=0)
    collated_batch["sky"] = torch.cat(all_skys, dim=0)

    assert collated_batch["image"].shape == (num_rays_per_batch, 3), collated_batch["image"].shape

    # Needed to correct the random indices to their actual camera idx locations.
    indices[:, 0] = batch["image_idx"][c]
    collated_batch["indices"] = indices  # with the abs camera indices

    if keep_full_image:
        collated_batch["full_image"] = batch["image"]

    return collated_batch


class PixelSampler:  # pylint: disable=too-few-public-methods
    """Samples 'pixel_batch's from 'image_batch's.

    Args:
        num_rays_per_batch: number of rays to sample per batch
        keep_full_image: whether or not to include a reference to the full image in returned batch
    """

    def __init__(self, num_rays_per_batch: int, keep_full_image: bool = False) -> None:
        self.num_rays_per_batch = num_rays_per_batch
        self.keep_full_image = keep_full_image

    def set_num_rays_per_batch(self, num_rays_per_batch: int):
        """Set the number of rays to sample per batch.

        Args:
            num_rays_per_batch: number of rays to sample per batch
        """
        self.num_rays_per_batch = num_rays_per_batch

    def sample(self, image_batch: Dict):
        """Sample an image batch and return a pixel batch.

        Args:
            image_batch: batch of images to sample from
        """
        if isinstance(image_batch["image"], BasicImages):
            image_batch = dict(image_batch.items())  # copy the dictioary so we don't modify the original
            image_batch["image"] = image_batch["image"].images
            if "mask" in image_batch:
                image_batch["mask"] = image_batch["mask"].images
            if "sky" in image_batch:
                image_batch["sky"] = image_batch["sky"].images
            pixel_batch = collate_image_dataset_batch_list(
                image_batch, self.num_rays_per_batch, keep_full_image=self.keep_full_image
            )
        elif isinstance(image_batch["image"], torch.Tensor):
            pixel_batch = collate_image_dataset_batch(
                image_batch, self.num_rays_per_batch, keep_full_image=self.keep_full_image
            )
        else:
            raise ValueError("image_batch['image'] must be a BasicImages or torch.Tensor")
        return pixel_batch
