#!/usr/bin/env python3
#SBATCH --gpus=55
#SBATCH --mem=12GB
#SBATCH --time=120:00:00
#SBATCH --mail-user=abstreik
#SBATCH --mail-type=ALL

import os
import multiprocessing
from solver import Solver
from model import Net, NetConnected
from model_nomatrix import NetNomatrix
from model_nomatrix3 import NetNomatrix16, NetNomatrix16V2
from dataset import RotationDataset
import lossfn
from torch.utils.data import DataLoader
import torch
import numpy as np
import math
import argparse
from experiment import run_experiment

norm_weights = [0, 1e-1, 1e0, 1e1, 1e2]

train_range = range(10, 21, 1)

SEED_TEST = 0
n_runs = 20


def mp_worker(data):
    dim, norm_weight, n_train = data

    for train_seed in range(1683, 1683 + n_runs, 1):
        checkpoint_dir = 'checkpoints/'
        checkpoint_dir += 'round2_normweight_model3_V2/'
        checkpoint_dir += 'dim-{}_normweight-{}_ntrain-{}_seed-{}/'.format(dim, norm_weight, n_train, train_seed)
        loss_fn = [{'loss_fn': lossfn.get_mse_loss(), 'weight': 1, 'label': 'mse'},
                   {'loss_fn': lossfn.get_norm_loss(), 'weight': norm_weight, 'label': 'norm'}]
        run_experiment(dim, n_train, train_seed, loss_fn, 0, checkpoint_dir, lr=5e-5, iterations=50000,
                       n_test=4096, model_class=NetNomatrix16V2, is_matrix_model=False)


def mp_handler():
    for dim in [2]:
        for norm_weight in norm_weights:
            train_range_spec = train_range
            if dim == 2:
                train_range_spec = range(10, 21, 1)
            if dim == 3:
                train_range_spec = range(20, 201, 20)

            for n_train in train_range_spec:
                p = multiprocessing.Process(target=mp_worker, args=((dim, norm_weight, n_train),))
                p.start()


if __name__ == '__main__':
    mp_handler()
