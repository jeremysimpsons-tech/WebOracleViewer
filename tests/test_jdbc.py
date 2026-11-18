import pytest

from app.errors import AppError
from app.jdbc import parse_connect_string


def test_jdbc_service():
    parsed = parse_connect_string("jdbc:oracle:thin:@dbhost.example.com:1521/ORCLPDB1")
    assert parsed.host == "dbhost.example.com"
    assert parsed.port == 1521
    assert parsed.service_name == "ORCLPDB1"
    assert parsed.protocol == "tcp"


def test_jdbc_double_slash_and_sid():
    easy = parse_connect_string("jdbc:oracle:thin:@//dbhost.example.com:1521/ORCLPDB1")
    assert easy.service_name == "ORCLPDB1"
    sid = parse_connect_string("jdbc:oracle:thin:@dbhost:1521:ORCL")
    assert sid.sid == "ORCL"
    assert sid.service_name is None


def test_jdbc_description_and_tcps():
    raw = (
        "jdbc:oracle:thin:@(DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)(HOST=adb.oraclecloud.com)"
        "(PORT=1522))(CONNECT_DATA=(SERVICE_NAME=gxxx_high.adb.oraclecloud.com)))"
    )
    parsed = parse_connect_string(raw)
    assert parsed.protocol == "tcps"
    assert parsed.dsn.startswith("(DESCRIPTION=")
    easy = parse_connect_string(
        "jdbc:oracle:thin:@tcps://adb.eu-zurich-1.oraclecloud.com:1522/abcd_prod.adb.oraclecloud.com"
    )
    assert easy.protocol == "tcps"
    assert easy.port == 1522
    assert easy.service_name == "abcd_prod.adb.oraclecloud.com"


def test_tns_admin_and_plain_easy_connect():
    parsed = parse_connect_string("jdbc:oracle:thin:@mydb_tp?TNS_ADMIN=/opt/oracle/wallet")
    assert parsed.tns_alias == "mydb_tp"
    assert parsed.wallet_dir == "/opt/oracle/wallet"
    plain = parse_connect_string("dbhost.example.com:1521/ORCLPDB1")
    assert plain.service_name == "ORCLPDB1"


def test_reject_unknown_jdbc():
    with pytest.raises(AppError):
        parse_connect_string("jdbc:postgresql://localhost/app")
