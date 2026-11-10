from datetime import datetime

import pytest
from opcua import ua
from opcua.common.connection import SecureConnection

from unilabos.devices.workstation.AI4M.base_opcua_client import (
    install_security_token_clock_guard,
)


def _token(token_id: int, created_at: datetime) -> ua.ChannelSecurityToken:
    token = ua.ChannelSecurityToken()
    token.TokenId = token_id
    token.CreatedAt = created_at
    token.RevisedLifetime = 3_600_000
    return token


def _connection() -> SecureConnection:
    install_security_token_clock_guard()
    connection = SecureConnection(ua.SecurityPolicy())
    connection.security_token = _token(44905, datetime(2026, 10, 20, 6, 0, 0))
    connection.next_security_token = _token(44906, datetime(2026, 10, 20, 6, 21, 0))
    connection.prev_security_token = _token(44903, datetime(2022, 1, 11, 13, 4, 39))
    connection._allow_prev_token = True
    return connection


def _header(token_id: int) -> ua.SymmetricAlgorithmHeader:
    header = ua.SymmetricAlgorithmHeader()
    header.TokenId = token_id
    return header


def test_previous_token_issued_by_stale_plc_clock_is_accepted():
    connection = _connection()
    connection._check_sym_header(_header(44903))


def test_unknown_security_token_is_still_rejected():
    connection = _connection()
    with pytest.raises(ua.UaError, match="Invalid security token"):
        connection._check_sym_header(_header(111))
