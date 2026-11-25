"""omi-local: billing is switched off on a self-hosted server, without breaking released clients.

Plan reads answer an unlimited local plan with the subscription UI hidden;
purchases, plan changes and portals answer 404 instead of reaching Stripe.
"""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.users import PlanType
from routers import payment as payment_router
from routers import users as users_router
from utils.other.endpoints import get_current_user_uid


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    app = FastAPI()
    app.include_router(payment_router.router)
    app.dependency_overrides[get_current_user_uid] = lambda: 'u'
    app.dependency_overrides[payment_router.auth.get_current_user_uid] = lambda: 'u'
    app.dependency_overrides[payment_router.auth.get_current_user_uid_no_byok_validation] = lambda: 'u'
    return TestClient(app)


@pytest.mark.parametrize(
    'method,path,body',
    [
        ('post', '/v1/payments/checkout-session', {'price_id': 'price_x'}),
        ('post', '/v1/payments/upgrade-subscription', {'price_id': 'price_x'}),
        ('delete', '/v1/payments/subscription', None),
        ('post', '/v1/payments/customer-portal', None),
        ('post', '/v1/stripe/connect-accounts', None),
        ('post', '/v1/payment-methods/default', {'payment_method_id': 'pm_x'}),
    ],
)
def test_billing_mutations_answer_not_available(client, monkeypatch, method, path, body):
    monkeypatch.setattr(payment_router.users_db, 'get_user_subscription', Mock(side_effect=AssertionError('no read')))
    response = getattr(client, method)(path, **({'json': body} if body is not None else {}))
    assert response.status_code == 404
    assert response.json()['detail'] == payment_router.BILLING_UNAVAILABLE


def test_billing_mutations_stay_enabled_when_hosted(monkeypatch):
    monkeypatch.delenv('OMI_SELF_HOSTED', raising=False)
    assert payment_router.require_hosted_billing() is None


def test_overage_info_is_a_zero_local_plan(client, monkeypatch):
    monkeypatch.setattr(payment_router.users_db, 'get_user_subscription', Mock(side_effect=AssertionError('no read')))
    body = client.get('/v1/payments/overage-info').json()
    assert body['plan_type'] == PlanType.unlimited.value
    assert body['is_overage_plan'] is False and body['overage_usd'] == 0.0


def test_subscription_is_unlimited_with_plan_ui_hidden(monkeypatch):
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setattr(users_router.users_db, 'is_byok_active', Mock(side_effect=AssertionError('no read')))
    already_read = {}
    response = users_router._user_subscription_response('u', 'ios', '1.0', already_read, None)
    assert response.subscription.plan == PlanType.unlimited
    assert response.show_subscription_ui is False and response.available_plans == []
    assert response.subscription.limits.transcription_seconds is None
    assert already_read == {'byok_active': False}


def test_chat_quota_is_unlimited(monkeypatch):
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setattr(users_router, 'get_customer_firestore_client', Mock(side_effect=AssertionError('no read')))
    quota = users_router.get_user_chat_usage_quota(uid='u', x_app_platform='macos')
    assert quota.allowed is True and quota.limit is None and quota.plan_type == PlanType.unlimited.value
