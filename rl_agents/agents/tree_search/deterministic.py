import numpy as np
import logging
from rl_agents.agents.common.factory import safe_deepcopy_env
from rl_agents.agents.tree_search.abstract import Node, AbstractTreeSearchAgent, AbstractPlanner

logger = logging.getLogger(__name__)


class DeterministicPlannerAgent(AbstractTreeSearchAgent):
    """
        An agent that performs optimistic planning in deterministic MDPs.
    """
    def make_planner(self):
        return OptimisticDeterministicPlanner(self.env, self.config)


class OptimisticDeterministicPlanner(AbstractPlanner):
    """
       An implementation of Open Loop Optimistic Planning.
    """
    def __init__(self, env, config=None):
        super(OptimisticDeterministicPlanner, self).__init__(config)
        self.env = env
        self.leaves = None

    @classmethod
    def default_config(cls):
        cfg = super().default_config()
        cfg.update({
            "restart": False,
            "ignore_terminal": True
        })
        return cfg

    def make_root(self):
        root = DeterministicNode(None, planner=self)
        self.leaves = [root]
        return root

    def run(self):
        """
            Run an OptimisticDeterministicPlanner episode
        """
        leaf_to_expand = max(self.leaves, key=lambda n: n.get_value_upper_bound())
        if not leaf_to_expand.done or self.config["ignore_terminal"]:
            leaf_to_expand.expand(self.leaves)

        leaf_to_expand.backup_to_root()

    def plan(self, state, observation):
        self.root.state = state
        self.reset_oracle_calls()
        for _ in range(int(np.ceil(self.config["budget"] / state.action_space.n))):
            self.run()
            if self.oracle_calls >= self.config["budget"]:
                break
        return self.get_plan()

    def step_by_subtree(self, action):
        super(OptimisticDeterministicPlanner, self).step_by_subtree(action)
        if not self.root.children:
            self.leaves = [self.root]
        #  v0 = r0 + g r1 + g^2 r2 +... and v1 = r1 + g r2 + ... = (v0-r0)/g
        for leaf in self.leaves:
            leaf.value = (leaf.value - self.root.reward) / self.config["gamma"]
            leaf.value_upper_bound = (leaf.value_upper_bound - self.root.reward) / self.config["gamma"]
        self.root.backup_values()


class DeterministicNode(Node):
    def __init__(self, parent, planner, state=None, depth=0):
        super(DeterministicNode, self).__init__(parent, planner)
        self.state = state
        self.depth = depth
        self.reward = 0
        self.value_upper_bound = 0
        self.count = 1
        self.done = False

    def selection_rule(self):
        if not self.children:
            return None
        actions = list(self.children.keys())
        index = self.random_argmax([self.children[a].get_value() for a in actions])
        return actions[index]

    def expand(self, leaves):
        if self.state is None:
            raise Exception("The state should be set before expanding a node")
        try:
            actions = range(self.state.action_space.n)  # self.state.get_available_actions()
        except AttributeError:
            actions = range(self.state.action_space.n)
        for action in actions:
            self.children[action] = type(self)(self,
                                               self.planner,
                                               state=safe_deepcopy_env(self.state),
                                               depth=self.depth + 1)

            # Fake restart: simulate from root down to self
            if self.planner.config["restart"]:
                for _ in range(self.depth):
                    self.planner.step_state(safe_deepcopy_env(self.children[action].state), action)
                    if self.planner.oracle_calls >= self.planner.config["budget"]:
                        return

            _, reward, done, _ = self.planner.step_state(self.children[action].state, action)
            self.children[action].update(reward, done)
            if self.planner.oracle_calls >= self.planner.config["budget"]:
                return

        leaves.remove(self)
        leaves.extend(self.children.values())

    def update(self, reward, done):
        if not np.all(0 <= reward) or not np.all(reward <= 1):
            raise ValueError("This planner assumes that all rewards are normalized in [0, 1]")
        gamma = self.planner.config["gamma"]
        self.reward = reward
        self.value = self.parent.value + (gamma ** (self.depth - 1)) * reward
        self.done = done
        self.value_upper_bound = self.value + (1 - done) * (gamma ** self.depth) / (1 - gamma)

    def backup_values(self):
        if self.children:
            backup_children = [child.backup_values() for child in self.children.values()]
            self.value = np.amax([b[0] for b in backup_children])
            self.value_upper_bound = np.amax([b[1] for b in backup_children])
        return self.get_value(), self.get_value_upper_bound()

    def backup_to_root(self):
        if self.parent:
            values = [(child.value, child.value_upper_bound) for child in self.parent.children.values()]
            self.parent.value = np.amax([b[0] for b in values])
            self.value_upper_bound = np.amax([b[1] for b in values])
            self.parent.backup_to_root()
            self.count += 1

    def get_value_upper_bound(self):
        return self.value_upper_bound
