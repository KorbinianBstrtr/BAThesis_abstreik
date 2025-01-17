from model import Net
from torch.optim import Adam
import torch.nn as nn
import torch
import numpy as np
import time
import os
from lossfn import get_mse_loss
from collections import Counter

1
class Solver:
    def __init__(self,
                 model,
                 loss_fn_train=[{'loss_fn': get_mse_loss(), 'weight': 1, 'label': 'mse'}],
                 loss_fn_test=[{'loss_fn': get_mse_loss(reduction='sum'), 'weight': 1, 'label': 'mse'}],
                 optim=Adam,
                 optim_args={'lr': 1e-3},
                 checkpoint_dir=None,
                 fn_matrix=lambda x: x,
                 fn_pred=lambda x: x,
                 is_matrix_model=True):
        torch.manual_seed(0)
        self.device = lambda: torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = model.to(self.device())
        self.loss_fn_train = loss_fn_train
        self.loss_fn_test = loss_fn_test
        self.fn_matrix = fn_matrix
        self.fn_pred = fn_pred
        self.save = False
        self.optimizer = optim(self.model.parameters(), **optim_args)
        self.is_matrix_model = is_matrix_model
        if checkpoint_dir is not None:
            self.save = True
            self.checkpoint_dir = checkpoint_dir
            if not os.path.exists(self.checkpoint_dir):
                os.makedirs(self.checkpoint_dir)
        self.epoch = 0
        self.iteration = 0

        self.init = True
        self.train_loss = torch.FloatTensor([10])
        self.idv_train_loss = {loss_dict['label']: torch.FloatTensor([10]) for loss_dict in loss_fn_train}
        self.start_time = None
        self.constraint_ln_weights = None

        self.hist = {'epochs': [],
                     'iterations': [],
                     'gradient_norm': [],
                     'train_loss': [],
                     'individual_train_losses': {loss_dict['label']: [] for loss_dict in loss_fn_train},
                     'individual_train_weights': {loss_dict['label']: [] for loss_dict in loss_fn_train},
                     'constraint_ln_weights': [],
                     'test_loss': [],
                     'individual_test_losses': {loss_dict['label']: [] for loss_dict in loss_fn_train},
                     'test_loss_no_fn': [],
                     'wall_times': []}

    def save_checkpoint(self, checkpoint_file, prints=True):
        if self.save:
            state_dict = {'model_states': self.model.state_dict(),
                          'optim': self.optimizer.state_dict(),
                          'epoch': self.epoch,
                          'iteration': self.iteration,
                          'hist': self.hist}

            checkpoint_path = self.checkpoint_dir + checkpoint_file
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            torch.save(state_dict, checkpoint_path)
            if prints:
                print("Model save: {}".format(checkpoint_path))

    def load_checkpoint(self, checkpoint_path):
        state_dict = torch.load(checkpoint_path, map_location=self.device())
        self.model.load_state_dict(state_dict['model_states'])
        self.optimizer.load_state_dict(state_dict['optim'])
        self.epoch = state_dict['epoch']
        self.iteration = state_dict['iteration']
        self.hist = state_dict['hist']

    def test(self, loader, prints=True):
        self.model.eval()
        with torch.no_grad():
            nr_samples = 0
            losses = [0] * len(self.loss_fn_test)
            indv_losses = [0] * len(self.loss_fn_train)
            # losses_no_fn = [0] * len(self.loss_fn_test)
            for (points, angles, points_rotated) in loader:
                points = points.to(self.device())
                angles = angles.to(self.device())
                points_rotated = points_rotated.to(self.device())
                nr_samples += points.shape[0]

                output_matrix, prediction = self.forward(angles, points)
                for i, loss_dict in enumerate(self.loss_fn_test):
                    losses[i] += loss_dict['weight'] * loss_dict['loss_fn'](prediction, points_rotated, output_matrix)

                for i, loss_dict in enumerate(self.loss_fn_train):
                    if loss_dict['label'] != 'det_sq':
                        continue
                    indv_losses[i] += loss_dict['loss_fn'](prediction, points_rotated, output_matrix)

            score = sum([loss / nr_samples for loss in losses])
            indv_losses = [l / nr_samples for l in indv_losses]
            # score_no_fn = sum([loss_no_fn / nr_samples for loss_no_fn in losses_no_fn])
            if len(self.hist['epochs']) == 0 or self.epoch != self.hist['epochs'][-1]:
                self.hist['epochs'].append(self.epoch)
                self.hist['iterations'].append(self.iteration)
                self.hist['gradient_norm'].append(self.get_grad_norm())
                self.hist['train_loss'].append(self.train_loss.item())
                for i, loss_dict in enumerate(self.loss_fn_train):
                    if loss_dict['label'] in self.hist['individual_train_losses']:
                        self.hist['individual_train_losses'][loss_dict['label']].append(
                            self.idv_train_loss[loss_dict['label']].item())
                        self.hist['individual_train_weights'][loss_dict['label']].append(loss_dict['weight'])
                        if loss_dict['label'] == 'det_sq':
                            self.hist['individual_test_losses'][loss_dict['label']].append(indv_losses[i].item())
                if self.constraint_ln_weights is not None and len(self.constraint_ln_weights) > 0:
                    self.hist['constraint_ln_weights'].append(np.copy(self.constraint_ln_weights[0].detach().numpy()))
                self.hist['test_loss'].append(score.item())
                # self.hist['test_loss_no_fn'].append(score_no_fn.item())
                self.hist['wall_times'].append(time.time() - self.start_time)
            if len(self.hist['test_loss']) == 1 or score < min(self.hist['test_loss'][:-1]):
                self.save_checkpoint('best.pkl', prints=prints)
            if prints:
                print("Epoch {}\tIteration {}".format(self.epoch, self.iteration))
                print("Train score = {}\tTest score = {}".format(self.train_loss.item(), score.item()))
            return score

    def forward(self, angles, points, apply_fns=True):
        if self.is_matrix_model:
            output_matrix = self.model(angles)
            if apply_fns:
                output_matrix = self.fn_matrix(output_matrix)
            prediction = torch.bmm(output_matrix, points.view((points.shape[0], points.shape[1], 1)))
            prediction = prediction.view((points.shape[0], points.shape[1]))
            if apply_fns:
                prediction = self.fn_pred(prediction)
            return output_matrix, prediction
        else:
            return None, self.model(torch.cat((angles, points), dim=1))

    def train(self, loader, epochs=None, iterations=None, test_every=False, test_every_iterations=False,
              test_loader=None, save_final=True, prints=True):
        assert (epochs is not None or iterations is not None)
        self.start_time = time.time()
        if epochs is not None:
            epochs += self.epoch
        if iterations is not None:
            iterations += self.iteration
        while (epochs is not None and self.epoch < epochs) or (iterations is not None and self.iteration < iterations):
            self.model.train()
            epoch_train_loss = 0
            epoch_idv_train_loss = Counter()
            for (points, angles, points_rotated) in loader:
                points = points.to(self.device())
                angles = angles.to(self.device())
                points_rotated = points_rotated.to(self.device())

                output_matrix, prediction = self.forward(angles, points)
                loss = 0
                for loss_dict in self.loss_fn_train:
                    loss_val = loss_dict['weight'] * loss_dict['loss_fn'](prediction, points_rotated, output_matrix)
                    epoch_idv_train_loss[loss_dict['label']] += loss_val
                    loss += loss_val
                epoch_train_loss += loss
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()
                if test_every_iterations and (self.iteration % test_every_iterations == 0):
                    self.test(test_loader, prints=prints)
                self.iteration += 1
            epoch_train_loss /= len(loader)
            self.train_loss = epoch_train_loss
            self.idv_train_loss = epoch_idv_train_loss

            if test_every and (self.epoch % test_every == 0):
                self.test(test_loader, prints=prints)

            self.epoch += 1

        if save_final:
            self.test(test_loader, prints=prints)
            self.save_checkpoint('final.pkl', prints=prints)

    def set_loss_fn_train(self, loss_fn_train):
        self.loss_fn_train = loss_fn_train
        if self.iteration == 0:
            self.hist['individual_train_losses'] = {loss_dict['label']: [] for loss_dict in loss_fn_train}
            self.hist['individual_train_weights'] = {loss_dict['label']: [] for loss_dict in loss_fn_train}
            self.hist['individual_test_losses'] = {loss_dict['label']: [] for loss_dict in loss_fn_train}
            self.hist['constraint_ln_weights'] = []
            self.idv_train_loss = {loss_dict['label']: torch.FloatTensor([10]) for loss_dict in loss_fn_train}

    def get_grad_norm(self):
        total_norm = 0
        for p in self.model.parameters():
            param_norm = p.grad.data.norm(2)
            total_norm += param_norm.item() ** 2
        total_norm = total_norm ** (1. / 2)
        return total_norm
