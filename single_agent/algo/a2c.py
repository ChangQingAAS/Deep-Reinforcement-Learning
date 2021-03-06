import gym
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical
import torch.multiprocessing as mp
import numpy as np
import sys

sys.path.append(".")
from args.config import default_params as params


class ActorCritic(nn.Module):
    def __init__(self):
        super(ActorCritic, self).__init__()
        self.fc1 = nn.Linear(4, 256)
        self.fc_pi = nn.Linear(256, 2)
        self.fc_v = nn.Linear(256, 1)

    def pi(self, x, softmax_dim=1):
        x = F.relu(self.fc1(x))
        x = self.fc_pi(x)
        prob = F.softmax(x, dim=softmax_dim)
        return prob

    def v(self, x):
        x = F.relu(self.fc1(x))
        v = self.fc_v(x)
        return v


def worker(worker_id, master_end, worker_end):
    master_end.close()  # Forbid worker to use the master end for messaging
    env = gym.make(params['gym_env'])
    env.seed(worker_id)

    while True:
        cmd, data = worker_end.recv()
        if cmd == 'step':
            ob, reward, done, info = env.step(data)
            if done:
                ob = env.reset()
            worker_end.send((ob, reward, done, info))
        elif cmd == 'reset':
            ob = env.reset()
            worker_end.send(ob)
        elif cmd == 'reset_task':
            ob = env.reset_task()
            worker_end.send(ob)
        elif cmd == 'close':
            worker_end.close()
            break
        elif cmd == 'get_spaces':
            worker_end.send((env.observation_space, env.action_space))
        else:
            raise NotImplementedError


class ParallelEnv:
    def __init__(self, n_train_processes):
        self.nenvs = n_train_processes
        self.waiting = False
        self.closed = False
        self.workers = list()

        master_ends, worker_ends = zip(*[mp.Pipe() for _ in range(self.nenvs)])
        self.master_ends, self.worker_ends = master_ends, worker_ends

        for worker_id, (master_end, worker_end) in enumerate(zip(master_ends, worker_ends)):
            p = mp.Process(target=worker, args=(worker_id, master_end, worker_end))
            p.daemon = True
            p.start()
            self.workers.append(p)

        # Forbid master to use the worker end for messaging
        for worker_end in worker_ends:
            worker_end.close()

    def step_async(self, actions):
        for master_end, action in zip(self.master_ends, actions):
            master_end.send(('step', action))
        self.waiting = True

    def step_wait(self):
        results = [master_end.recv() for master_end in self.master_ends]
        self.waiting = False
        obs, rews, dones, infos = zip(*results)
        return np.stack(obs), np.stack(rews), np.stack(dones), infos

    def reset(self):
        for master_end in self.master_ends:
            master_end.send(('reset', None))
        return np.stack([master_end.recv() for master_end in self.master_ends])

    def step(self, actions):
        self.step_async(actions)
        return self.step_wait()

    def close(self):  # For clean up resources
        if self.closed:
            return
        if self.waiting:
            [master_end.recv() for master_end in self.master_ends]
        for master_end in self.master_ends:
            master_end.send(('close', None))
        for worker in self.workers:
            worker.join()
            self.closed = True


def test(step_idx, model):
    env = gym.make(params['gym_env'])
    score = 0.0
    done = False
    num_test = 10

    for _ in range(num_test):
        s = env.reset()
        while not done:
            prob = model.pi(torch.from_numpy(s).float(), softmax_dim=0)
            a = Categorical(prob).sample().numpy()
            s_prime, r, done, info = env.step(a)
            s = s_prime
            score += r
        done = False
    print(f"Step # :{step_idx}, avg score : {score/num_test:.1f}")
    with open("./result/a2c.csv", "a+", encoding="utf-8") as f:
        f.write("{},{}\n".format(step_idx, score / num_test))

    env.close()


def compute_target(v_final, r_lst, mask_lst, gamma):
    G = v_final.reshape(-1)
    td_target = list()

    for r, mask in zip(r_lst[::-1], mask_lst[::-1]):
        G = r + gamma * G * mask
        td_target.append(G)

    return torch.tensor(td_target[::-1]).float()


class a2c_algo():
    def __init__(self):
        super(a2c_algo, self).__init__()
        self.n_train_processes = params['n_train_processes']
        self.learning_rate = params['learning_rate']
        self.max_train_steps = params['max_train_steps']
        self.update_interval = params['update_interval']
        self.gamma = params['gamma']
        self.print_interval = self.update_interval * 10
        self.model = ActorCritic()
        self.envs = ParallelEnv(self.n_train_processes)
        self.optimizer = optim.Adam(self.model.parameters(), lr=self.learning_rate)

    def init_write(self):
        with open("./result/a2c.csv", "w+", encoding="utf-8") as f:
            f.write("steps,reward\n")

    def train(self):
        self.init_write()
        step_idx = 0
        s = self.envs.reset()
        while step_idx < self.max_train_steps:
            s_lst, a_lst, r_lst, mask_lst = list(), list(), list(), list()
            for _ in range(self.update_interval):
                prob = self.model.pi(torch.from_numpy(s).float())
                a = Categorical(prob).sample().numpy()
                s_prime, r, done, info = self.envs.step(a)

                s_lst.append(s)
                a_lst.append(a)
                r_lst.append(r)
                mask_lst.append(1 - done)

                s = s_prime
                step_idx += 1

            s_final = torch.from_numpy(s_prime).float()
            v_final = self.model.v(s_final).detach().clone().numpy()
            td_target = compute_target(v_final, r_lst, mask_lst, self.gamma)

            td_target_vec = td_target.reshape(-1)
            s_vec = torch.tensor(s_lst).float().reshape(-1, 4)  # 4 == Dimension of state
            a_vec = torch.tensor(a_lst).reshape(-1).unsqueeze(1)
            advantage = td_target_vec - self.model.v(s_vec).reshape(-1)

            pi = self.model.pi(s_vec, softmax_dim=1)
            pi_a = pi.gather(1, a_vec).reshape(-1)
            loss = -(torch.log(pi_a) * advantage.detach()).mean() +\
                F.smooth_l1_loss(self.model.v(s_vec).reshape(-1), td_target_vec)

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            if step_idx % self.print_interval == 0:
                test(step_idx, self.model)

        self.envs.close()


if __name__ == "__main__":
    algo = a2c_algo()
    algo.train()