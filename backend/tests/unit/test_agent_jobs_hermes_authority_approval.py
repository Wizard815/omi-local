"""Hermes' session-authority runs ask for approval in ``pending_controls`` and answer only
``{request_id: prompt_id, execution_generation, choice}``.

Shapes recorded from a real hermes-agent gateway (API server, 2026-11): Omi read only the
legacy ``approval`` object, so the question showed with no command and Allow once was
refused with ``invalid_params``.
"""

from utils import agent_jobs

AUTHORITY_STATUS = {
    'object': 'hermes.run',
    'run_id': 'run_c6b6',
    'status': 'waiting_for_approval',
    'tool': 'terminal',
    'pending_controls': [
        {
            'kind': 'approval',
            'prompt_id': '5a05d36db0d34e9b884b03597b81814a',
            'execution_generation': 1,
            'command': 'rm -rf /tmp/omi-hermes-probe-dir',
            'description': 'delete in root path',
            'choices': ['once', 'deny', 'session', 'always'],
        }
    ],
    'execution_generation': 1,
    'output': '',
}

LEGACY_STATUS = {
    'status': 'waiting_for_approval',
    'approval': {
        'request_id': 'req-1',
        'command': 'python3 x.py',
        'description': 'Run it',
        'choices': ['once', 'deny'],
    },
}


def test_authority_run_question_and_answer_shape():
    approval = agent_jobs.hermes_approval(AUTHORITY_STATUS)
    assert approval == {
        'request_id': '5a05d36db0d34e9b884b03597b81814a',
        'command': 'rm -rf /tmp/omi-hermes-probe-dir',
        'description': 'delete in root path',
        'choices': ['once', 'deny'],
        'execution_generation': 1,
    }
    assert agent_jobs.hermes_approval_answer(approval, 'once') == {
        'choice': 'once',
        'request_id': '5a05d36db0d34e9b884b03597b81814a',
        'execution_generation': 1,
    }


def test_legacy_run_question_and_answer_shape():
    approval = agent_jobs.hermes_approval(LEGACY_STATUS)
    assert approval['request_id'] == 'req-1' and approval['command'] == 'python3 x.py'
    assert 'execution_generation' not in approval
    assert agent_jobs.hermes_approval_answer(approval, 'deny') == {'choice': 'deny', 'request_id': 'req-1'}
    assert agent_jobs.hermes_approval_answer({}, 'deny') == {'choice': 'deny'}
