import gym
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical
import sys
sys.path.append(".")
from args.config import ppo_params as params


class PPO(nn.Module):

    def __init__(self, learning_rete, gamma, lmbda, K_epoch, T_horizon, eps_clip, in_dim, out_dim):
        super(PPO, self).__init__()
        self.learning_rate = learning_rete
        self.gamma = gamma
        self.lmbda = lmbda
        self.K_epoch = K_epoch
        self.T_horizon = T_horizon
        self.eps_clip = eps_clip
        self.data = []

        self.fc1 = nn.Linear(in_dim, 256)
        self.fc_pi = nn.Linear(256, 2)
        self.fc_v = nn.Linear(256, out_dim)
        self.optimizer = optim.Adam(self.parameters(), lr=self.learning_rate)

    def pi(self, x, softmax_dim=0):
        x = F.relu(self.fc1(x))
        x = self.fc_pi(x)
        prob = F.softmax(x, dim=softmax_dim)
        return prob

    def v(self, x):
        x = F.relu(self.fc1(x))
        v = self.fc_v(x)
        return v

    def put_data(self, transition):
        self.data.append(transition)

    def make_batch(self):
        s_lst, a_lst, r_lst, s_prime_lst, prob_a_lst, done_lst = [], [], [], [], [], []
        for transition in self.data:
            s, a, r, s_prime, prob_a, done = transition

            s_lst.append(s)
            a_lst.append([a])
            r_lst.append([r])
            s_prime_lst.append(s_prime)
            prob_a_lst.append([prob_a])
            done_mask = 0 if done else 1
            done_lst.append([done_mask])

        s,a,r,s_prime,done_mask, prob_a = torch.tensor(s_lst, dtype=torch.float), torch.tensor(a_lst), \
                                          torch.tensor(r_lst), torch.tensor(s_prime_lst, dtype=torch.float), \
                                          torch.tensor(done_lst, dtype=torch.float), torch.tensor(prob_a_lst)
        self.data = []
        return s, a, r, s_prime, done_mask, prob_a

    def train_net(self):
        s, a, r, s_prime, done_mask, prob_a = self.make_batch()

        for i in range(self.K_epoch):
            td_target = r + self.gamma * self.v(s_prime) * done_mask
            delta = td_target - self.v(s)
            delta = delta.detach().numpy()

            advantage_lst = []
            advantage = 0.0
            for delta_t in delta[::-1]:
                advantage = self.gamma * self.lmbda * advantage + delta_t[0]
                advantage_lst.append([advantage])
            advantage_lst.reverse()
            advantage = torch.tensor(advantage_lst, dtype=torch.float)

            pi = self.pi(s, softmax_dim=1)
            pi_a = pi.gather(1, a)
            ratio = torch.exp(torch.log(pi_a) - torch.log(prob_a))  # a/b == exp(log(a)-log(b))

            surr1 = ratio * advantage
            surr2 = torch.clamp(ratio, 1 - self.eps_clip, 1 + self.eps_clip) * advantage
            loss = -torch.min(surr1, surr2) + F.smooth_l1_loss(self.v(s), td_target.detach())

            self.optimizer.zero_grad()
            loss.mean().backward()
            self.optimizer.step()


class PPO_algo():

    def __init__(self, path):
        super(PPO_algo, self).__init__()
        self.path = path
        self.env = gym.make(params['gym_env'])
        self.print_interval = params["print_interval"]
        self.epoch = params["epoch"]
        self.learning_rate = params["learning_rate"]
        self.gamma = params["gamma"]
        self.lmbda = params["lmbda"]
        self.K_epoch = params["K_epoch"]
        self.T_horizon = params["T_horizon"]
        self.eps_clip = params["eps_clip"]
        self.train_number = params['train_number']
        self.obs_dim = self.env.observation_space.shape[0]
        self.action_dim = self.env.action_space.n
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = PPO(self.learning_rate, self.gamma, self.lmbda, self.K_epoch, self.T_horizon, self.eps_clip,
                         self.obs_dim, self.action_dim).to(self.device)

        self.init_write()

    def init_write(self):
        for i in range(self.train_number):
            with open(self.path + "/result/PPO/result_%s.csv" % str(i), "w+", encoding="utf-8") as f:
                f.write("epoch_number,average reward\n")

    def train(self):
        for train_counter in range(self.train_number):
            score = 0.0
            for n_epi in range(self.epoch):
                s = self.env.reset()
                done = False
                while not done:
                    for t in range(self.T_horizon):
                        prob = self.model.pi(torch.from_numpy(s).float())
                        m = Categorical(prob)
                        a = m.sample().item()
                        s_prime, r, done, info = self.env.step(a)

                        self.model.put_data((s, a, r, s_prime, prob[a].item(), done))
                        s = s_prime

                        score += r
                        if done:
                            break

                    self.model.train_net()

                if n_epi % self.print_interval == 0:
                    print("episode :{}, avg score : {:.1f}".format(n_epi, score / self.print_interval))
                    with open(self.path + "/result/PPO/result_%s.csv" % str(train_counter), "a+",
                              encoding="utf-8") as f:
                        f.write("{},{}\n".format(n_epi, score / self.print_interval))
                    score = 0.0

            self.env.close()


if __name__ == '__main__':
    path = sys.path[0].rsplit("/", 1)[0]
    algo = PPO_algo(path)
    algo.train()