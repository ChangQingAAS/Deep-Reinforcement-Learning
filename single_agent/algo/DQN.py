import gym
import collections
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import sys

sys.path.append(".")
from args.config import dqn_params as params


class ReplayBuffer():

    def __init__(self, buffer_limit):
        self.buffer = collections.deque(maxlen=buffer_limit)

    def put(self, transition):
        self.buffer.append(transition)

    def sample(self, n):
        mini_batch = random.sample(self.buffer, n)
        s_lst, a_lst, r_lst, s_prime_lst, done_mask_lst = [], [], [], [], []

        for transition in mini_batch:
            s, a, r, s_prime, done_mask = transition
            s_lst.append(s)
            a_lst.append([a])
            r_lst.append([r])
            s_prime_lst.append(s_prime)
            done_mask_lst.append([done_mask])

        return torch.tensor(s_lst, dtype=torch.float), torch.tensor(a_lst), \
               torch.tensor(r_lst), torch.tensor(s_prime_lst, dtype=torch.float), \
               torch.tensor(done_mask_lst)

    def size(self):
        return len(self.buffer)


class Qnet(nn.Module):

    def __init__(self, in_dim, out_dim):
        super(Qnet, self).__init__()
        self.layers = nn.Sequential(nn.Linear(in_dim, 128), nn.ReLU(), nn.Linear(128, 128),
                                    nn.ReLU(), nn.Linear(128, out_dim))

    def forward(self, x):
        x = self.layers(x)
        return x

    def sample_action(self, obs, epsilon):
        out = self.forward(obs)
        coin = random.random()
        if coin < epsilon:
            return random.randint(0, 1)
        else:
            return out.argmax().item()


class DQN_ALGO():

    def __init__(self, path):
        super(DQN_ALGO, self).__init__()
        self.path = path
        self.env = gym.make(params['gym_env'])
        self.print_interval = params["print_interval"]
        self.epoch = params["epoch"]
        self.learning_rate = params["learning_rate"]
        self.gamma = params["gamma"]
        self.n_rollout = params["n_rollout"]
        self.batch_size = params["batch_size"]
        self.train_number = params['train_number']
        self.memory = ReplayBuffer(params["buffer_limit"])
        self.obs_dim = self.env.observation_space.shape[0]
        self.action_dim = self.env.action_space.n
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.q = Qnet(self.obs_dim, self.action_dim).to(self.device)
        self.q_target = Qnet(self.obs_dim, self.action_dim).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())

        self.optimizer = optim.Adam(self.q.parameters(), lr=self.learning_rate)

        self.init_write()

    def init_write(self):
        for i in range(self.train_number):
            with open(self.path + "/result/DQN/result_%s.csv" % str(i), "w+",
                      encoding="utf-8") as f:
                f.write("epoch_number,average reward\n")

    def train_(self, q, q_target, memory, optimizer):
        for item in range(self.n_rollout):
            s, a, r, s_prime, done_mask = memory.sample(self.batch_size)

            q_out = q(s)
            q_a = q_out.gather(1, a)
            max_q_prime = q_target(s_prime).max(1)[0].unsqueeze(1)
            target = r + self.gamma * max_q_prime * done_mask
            loss = F.smooth_l1_loss(q_a, target)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

    def train(self):
        for train_counter in range(self.train_number):
            score = 0.0
            for n_epi in range(self.epoch):
                #Linear annealing from 8% to 1%
                epsilon = max(0.01, 0.08 - 0.01 * (n_epi / 200))
                s = self.env.reset()
                done = False

                while not done:
                    a = self.q.sample_action(torch.from_numpy(s).float(), epsilon)
                    # print("action is ", a)
                    s_prime, r, done, info = self.env.step(a)
                    done_mask = 0.0 if done else 1.0
                    self.memory.put((s, a, r, s_prime, done_mask))
                    s = s_prime

                    score += r
                    if done:
                        break

                if self.memory.size() > 2000:
                    self.train_(self.q, self.q_target, self.memory, self.optimizer)

                if n_epi % self.print_interval == 0:
                    self.q_target.load_state_dict(self.q.state_dict())
                    with open(self.path + "/result/DQN/result_%s.csv" % str(train_counter),
                              "a+",
                              encoding="utf-8") as f:
                        f.write("{},{}\n".format(n_epi, score / self.print_interval))
                    print(
                        "episode :{},average  score : {:.1f}, n_buffer : {}, eps : {:.1f}%".format(
                            n_epi, score / self.print_interval, self.memory.size(), epsilon * 100))
                    score = 0.0
            self.env.close()


if __name__ == '__main__':
    path = sys.path[0].rsplit("/", 1)[0]
    algo = DQN_ALGO(path)
    algo.train()