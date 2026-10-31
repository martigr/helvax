from datetime import date

import pytest

from helvax.dates import Duration, add_months, age_label, age_years, fmt_date, parse_day


@pytest.mark.parametrize('d, months, expected', [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2024, 1, 31), 1, date(2024, 2, 29)),
    (date(2026, 11, 15), 2, date(2027, 1, 15)),
    (date(2026, 3, 31), -1, date(2026, 2, 28)),
    (date(2024, 2, 29), 12, date(2025, 2, 28)),
])
def test_add_months(d, months, expected):
    assert add_months(d, months) == expected


def test_duration():
    assert Duration.parse('6w').after(date(2026, 1, 1)) == date(2026, 2, 12)
    assert Duration.parse('12m').after(date(2026, 1, 31)) == date(2027, 1, 31)
    assert Duration.parse('25y').after(date(2000, 2, 29)) == date(2025, 2, 28)
    assert Duration.parse(' 14d ').after(date(2026, 1, 1)) == date(2026, 1, 15)
    assert Duration.parse('1y') == Duration(1, 'y')
    assert [Duration.parse(x).label() for x in ('1d', '2w', '1m', '12m', '1y', '5y')] == [
        '1 Tag', '2 Wochen', '1 Monat', '12 Monate', '1 Jahr', '5 Jahre']
    for bad in ('', '6', 'w', '-1m', '1.5y', '3h'):
        with pytest.raises(ValueError):
            Duration.parse(bad)


def test_ages():
    birth = date(2025, 3, 15)
    assert age_years(birth, date(2026, 3, 14)) == 0
    assert age_years(birth, date(2026, 3, 15)) == 1
    assert age_label(birth, date(2025, 3, 20)) == '0 Wochen'
    assert age_label(birth, date(2025, 3, 22)) == '1 Woche'
    assert age_label(birth, date(2025, 5, 14)) == '8 Wochen'
    assert age_label(birth, date(2025, 5, 15)) == '2 Monate'
    assert age_label(birth, date(2027, 3, 14)) == '23 Monate'
    assert age_label(birth, date(2027, 3, 15)) == '2 Jahre'
    assert age_label(birth, date(2025, 1, 1)) == 'noch nicht geboren'


def test_parse_and_format():
    assert parse_day('2026-10-15') == date(2026, 10, 15)
    for bad in ('', None, '15.10.2026', '1899-12-31', '3000-01-01', '2026-02-30'):
        assert parse_day(bad) is None
    assert fmt_date(date(2026, 3, 5)) == '05.03.2026'
    assert fmt_date(None) == ''
