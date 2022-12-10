import os
from os import path
import argparse
from multiprocessing import cpu_count
import torch
import torch.optim as optim
from torch.optim.lr_scheduler import StepLR, MultiStepLR
from ..models import create_model, save_model, load_model
from ..initializer import set_seed
from abc import ABC, abstractmethod


class Trainer(ABC):
    def __init__(self, args):
        self.args = args
        self.initialized = False

    def initialize(self):
        if self.initialized:
            return
        self.initialized = True
        if self.args.gpu[0] >= 0:
            self.device = f"cuda:{self.args.gpu[0]}"
        else:
            self.device = "cpu"
        os.makedirs(self.args.model_dir, exist_ok=True)
        set_seed(self.args.seed)

        self.model = self.create_model()
        self.train_loader = self.create_dataloader(type="train")
        self.eval_loader = self.create_dataloader(type="eval")
        self.optimizer = self.create_optimizer()
        self.scheduler = self.create_scheduler()
        self.grad_scaler = self.create_grad_scaler()
        self.best_model_filename = self.create_best_model_filename()
        self.epoch = 1
        self.start_epoch = 1
        self.best_loss = 1000000000
        if self.args.resume:
            self.resume()
        self.env = self.create_env()
        if not (self.args.disable_amp or self.device == "cpu"):
            self.env.enable_amp()

    def resume(self):
        latest_checkpoint_filename = self.create_checkpoint_filename()
        _, meta = load_model(latest_checkpoint_filename, model=self.model)
        if not self.args.reset_state:
            self.optimizer.load_state_dict(meta["optimizer_state_dict"])
            self.scheduler.load_state_dict(meta["scheduler_state_dict"])
            self.grad_scaler.load_state_dict(meta["grad_scaler_state_dict"])
            self.start_epoch = meta["last_epoch"] + 1
            self.best_loss = meta["best_loss"]
        print(f"* load checkpoint from {latest_checkpoint_filename}")

    def fit(self):
        self.initialize()
        for self.epoch in range(self.start_epoch, self.args.max_epoch):
            print("-" * 64)
            print(f" epoch: {self.epoch}, lr: {self.scheduler.get_last_lr()}")
            print("--\n train")
            self.env.train(
                loader=self.train_loader,
                optimizer=self.optimizer,
                grad_scaler=self.grad_scaler)
            self.scheduler.step()

            print("--\n eval")
            loss = self.env.eval(self.eval_loader)
            if loss < self.best_loss:
                print("* best model updated")
                self.best_loss = loss
                self.save_best_model()
            self.save_checkpoint()

    def create_model(self):
        return create_model(self.args.arch, device_ids=self.args.gpu)

    def create_optimizer(self):
        # TODO: support more optimizer if needed
        if self.args.optimizer == "adam":
            return optim.Adam(self.model.parameters(), lr=self.args.learning_rate)
        elif self.args.optimizer == "sgd":
            return optim.SGD(self.model.parameters(), lr=self.args.learning_rate)
        else:
            raise NotImplementedError(f"optimizer = {self.args.optimizer}")

    def create_scheduler(self):
        # TODO: support more schedulers if needed
        if len(self.args.learning_rate_decay_step) == 1:
            return StepLR(
                self.optimizer,
                step_size=self.args.learning_rate_decay_step[0],
                gamma=self.args.learning_rate_decay)
        else:
            return MultiStepLR(
                self.optimizer,
                milestones=self.args.learning_rate_decay_step,
                gamma=self.args.learning_rate_decay)

    def create_grad_scaler(self):
        return torch.cuda.amp.GradScaler()

    def create_best_model_filename(self):
        return path.join(self.args.model_dir, f"{self.args.arch}.pth")

    def create_checkpoint_filename(self):
        return path.join(self.args.model_dir, f"{self.args.arch}.checkpoint.pth")

    def save_checkpoint(self):
        save_model(
            self.model,
            self.create_checkpoint_filename(),
            train_kwargs=self.args,
            optimizer_state_dict=self.optimizer.state_dict(),
            scheduler_state_dict=self.scheduler.state_dict(),
            grad_scaler_state_dict=self.grad_scaler.state_dict(),
            best_loss=self.best_loss,
            last_epoch=self.epoch)

    def save_best_model(self):
        save_model(self.model, self.best_model_filename, train_kwargs=self.args)

    @abstractmethod
    def create_dataloader(self, type):
        assert (type in {"train", "eval"})

    @abstractmethod
    def create_env(self):
        pass


def create_trainer_default_parser():
    parser = argparse.ArgumentParser(
        add_help=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    num_workers = cpu_count() - 2
    if not num_workers > 0:
        num_workers = cpu_count

    parser.add_argument("--data-dir", "-i", type=str, required=True,
                        help="input training data directory that created by `create_training_data` command")
    parser.add_argument("--model-dir", type=str, required=True,
                        help="output directory for trained model/checkpoint")
    parser.add_argument("--minibatch-size", type=int, default=64,
                        help="minibatch size")
    parser.add_argument("--optimizer", type=str, choices=["adam", "sgd"], default="adam",
                        help="optimizer")
    parser.add_argument("--num-workers", type=int, default=num_workers,
                        help="number of worker processes for data loader")
    parser.add_argument("--max-epoch", type=int, default=200,
                        help="max epoch")
    parser.add_argument("--gpu", type=int, nargs="+", default=[0],
                        help="device ids; if -1 is specified, use CPU")
    parser.add_argument("--learning-rate", type=float, default=0.00025,
                        help="learning rate")
    parser.add_argument("--learning-rate-decay", type=float, default=0.995,
                        help="learning rate decay")
    parser.add_argument("--learning-rate-decay-step", type=int, nargs="+", default=[1],
                        help="learning rate decay step; if multiple values are specified, use MultiStepLR")
    parser.add_argument("--disable-amp", action="store_true",
                        help="disable AMP for some special reason")
    parser.add_argument("--resume", action="store_true",
                        help="resume training from the latest checkpoint file")
    parser.add_argument("--reset-state", action="store_true",
                        help="do not load best_score, optimizer and scheduler state when --resume")
    parser.add_argument("--seed", type=int, default=71,
                        help="random seed")

    return parser
