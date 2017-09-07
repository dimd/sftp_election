"""Orchestrator module.

Declare all the possible states and transitions between them.
Create a Seeker object and run its transitions forever.

Attributes:
    transitions (dict): A dict with all the possible states as keys.
        Each state has another dict as a value. That dict value has three keys.
        An action, a next_true and a next_false.
        An action must be a callable that returns either True or False.
        next_true is the next state that the state machine must transit if the
        action returned True.
        next_false is the next state that the state machine must transit if the
        action returned False.
        exit is a special state that just terminates the daemon. So next states
        are not required.

"""
from .seeker import Seeker


transitions = {
    'initial': {
        'action': 'is_charging_env',
        'next_true': 'discover_an_entry',
        'next_false': 'legacy_functionality'
    },
    'legacy_functionality': {
        'action': 'trigger_legacy',
        'next_true': 'exit',
        'next_false': 'initial'
    },
    'discover_an_entry': {
        'action': 'discover_an_entry_in_etcd',
        'next_true': 'watch',
        'next_false': 'discover_an_entry'
    },
    'watch': {
        'action': 'watch_in_a_thread',
        'next_false': 'discover_an_entry'
    },
    'exit': {
        'action': 'exit',
    }
}


def run():
    """Run the seeker's transitions forever."""
    seeker = Seeker(transitions, init_state='initial')

    while True:
        seeker.run_state_action()
