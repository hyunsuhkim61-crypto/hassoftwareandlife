import streamlit as st
import datetime as dt
import calendar
import os
import hmac
import hashlib
import base64

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# ==================== 기본 설정 ====================
st.write("DEBUG st.secrets keys:", list(st.secrets.keys()))
st.write("DEBUG redirect_uri:", st.secrets["google_oauth"]["redirect_uri"])
st.write("DEBUG client_id prefix:", st.secrets["google_oauth"]["client_id"][:20])


st.set_page_config(
    page_title="일정? 바로잡 GO!",
    page_icon="📅",
    layout="centered",
)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

today = dt.date.today()

# 세션 상태 초기화
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "creds" not in st.session_state:
    st.session_state.creds = None
if "cal_year" not in st.session_state:
    st.session_state.cal_year = today.year
if "cal_month" not in st.session_state:
    st.session_state.cal_month = today.month
if "selected_date" not in st.session_state:
    st.session_state.selected_date = today

# ==================== 스타일 ====================
st.markdown(
    """
    <style>
    .title-text {
        font-size: 2rem;
        font-weight: 800;
        color: #f5f5f5;
        margin: 0.8rem 0 0.5rem 0;
    }
    .pill-input > div > input {
        border-radius: 999px !important;
    }
    .pill-button > button {
        border-radius: 999px !important;
        font-weight: 600;
        padding: 0.6rem 2.0rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==================== OAuth Flow 도우미 ====================
def make_flow() -> Flow:
    """secrets.toml에 저장된 정보로 OAuth Flow 객체 만들기"""
    cfg = {
        "web": {
            "client_id": st.secrets["google_oauth"]["client_id"],
            "client_secret": st.secrets["google_oauth"]["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [st.secrets["google_oauth"]["redirect_uri"]],
        }
    }
    flow = Flow.from_client_config(cfg, scopes=SCOPES)
    flow.redirect_uri = st.secrets["google_oauth"]["redirect_uri"]
    return flow


def get_calendar_service():
    if st.session_state.creds is None:
        return None
    return build("calendar", "v3", credentials=st.session_state.creds)


# ==================== CSRF용 state 생성/검증 ====================
def generate_state() -> str:
    """
    서버 비밀키(state_secret)로 서명된 state 문자열 생성.
    세션에 저장할 필요 없이, 나중에 서명만 검증하면 됨.
    """
    secret_key = st.secrets["google_oauth"]["state_secret"].encode("utf-8")
    nonce = os.urandom(16)  # 랜덤 16바이트
    sig = hmac.new(secret_key, nonce, hashlib.sha256).digest()
    data = nonce + sig  # 총 16 + 32 = 48바이트
    return base64.urlsafe_b64encode(data).decode("utf-8")


def verify_state(state_str: str) -> bool:
    """
    구글에서 돌려준 state 문자열이 우리가 만든 것인지 검증.
    (nonce + HMAC 서명 구조인지 확인)
    """
    try:
        data = base64.urlsafe_b64decode(state_str.encode("utf-8"))
    except Exception:
        return False

    if len(data) != 48:
        return False

    nonce = data[:16]
    sig = data[16:]

    secret_key = st.secrets["google_oauth"]["state_secret"].encode("utf-8")
    expected_sig = hmac.new(secret_key, nonce, hashlib.sha256).digest()
    return hmac.compare_digest(sig, expected_sig)


def fetch_month_event_days(service, year: int, month: int):
    """주어진 연/월에 '일정이 있는 날짜(day 숫자)' 집합 반환"""
    if service is None:
        return set()

    from datetime import datetime, timezone

    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)

    time_min = datetime.combine(start, dt.time(0, 0), tzinfo=timezone.utc).isoformat()
    time_max = datetime.combine(end, dt.time(0, 0), tzinfo=timezone.utc).isoformat()

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    items = events_result.get("items", [])
    days = set()
    for event in items:
        start_info = event.get("start", {})
        date_str = start_info.get("date") or start_info.get("dateTime")
        if not date_str:
            continue
        date_only = date_str[:10]
        try:
            y, m, d = map(int, date_only.split("-"))
            days.add(d)
        except Exception:
            continue
    return days


# ==================== 1. OAuth 콜백 처리 (code + state 검증) ====================
# 🔁 실험용 API → 정식 API로 변경
params = st.query_params
code = params.get("code", [None])[0]
state_from_google = params.get("state", [None])[0]

if code and state_from_google and not st.session_state.logged_in:
    # CSRF 방어: state 서명 검증
    if not verify_state(state_from_google):
        st.error("OAuth state 검증에 실패했습니다. 다시 로그인해 주세요.")
        # st.experimental_set_query_params() 대체
        st.query_params.clear()
    else:
        try:
            flow = make_flow()
            flow.fetch_token(code=code)
            st.session_state.creds = flow.credentials
            st.session_state.logged_in = True
            # URL 정리
            st.query_params.clear()
        except Exception as e:
            st.error("구글 로그인 중 오류가 발생했습니다. 다시 시도해 주세요.")
            st.write(e)
            st.query_params.clear()

# ==================== 상단: 제목 + 로그인 버튼 ====================
top_left, top_right = st.columns([4, 1])

with top_left:
    st.markdown('<div class="title-text">일정? 바로잡 GO!</div>', unsafe_allow_html=True)

with top_right:
    if st.session_state.logged_in:
        st.success("구글 로그인 완료 ✅")
    else:
        if st.button("구글로 로그인"):
            flow = make_flow()
            state = generate_state()
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
                state=state,
            )
            # 현재 탭에서 바로 구글 로그인 페이지로 이동
            st.markdown(
                f'<meta http-equiv="refresh" content="0; url={auth_url}">',
                unsafe_allow_html=True,
            )
            st.stop()

st.write("")

service = get_calendar_service() if st.session_state.logged_in else None

# ==================== 가운데: 항상 펼쳐진 달력 ====================
st.subheader("캘린더")

if not st.session_state.logged_in:
    st.caption("구글 로그인 전에는 날짜만 선택 가능한 일반적인 캘린더입니다.")
else:
    st.caption("구글 캘린더에 일정이 있는 날에는 ● 점이 표시됩니다.")

year = st.session_state.cal_year
month = st.session_state.cal_month

# ---- 월 이동 헤더 ----
cal_top_left, cal_top_mid, cal_top_right = st.columns([1, 3, 1])

with cal_top_left:
    if st.button("◀ 이전달"):
        if month == 1:
            st.session_state.cal_month = 12
            st.session_state.cal_year -= 1
        else:
            st.session_state.cal_month -= 1

with cal_top_mid:
    st.markdown(f"### {year}년 {month}월")

with cal_top_right:
    if st.button("다음달 ▶"):
        if month == 12:
            st.session_state.cal_month = 1
            st.session_state.cal_year += 1
        else:
            st.session_state.cal_month += 1

# 업데이트된 값 다시 읽기
year = st.session_state.cal_year
month = st.session_state.cal_month

# 이 달의 일정 있는 날짜들
days_with_events = fetch_month_event_days(service, year, month) if service else set()

# ---- 요일 헤더 ----
weekday_cols = st.columns(7)
weekdays = ["일", "월", "화", "수", "목", "금", "토"]
for i, wd in enumerate(weekdays):
    with weekday_cols[i]:
        st.markdown(f"**{wd}**")

# ---- 달력 그리드 ----
cal = calendar.Calendar(firstweekday=6)  # 6=일요일
weeks = cal.monthdayscalendar(year, month)

for week in weeks:
    cols = st.columns(7)
    for i, day in enumerate(week):
        with cols[i]:
            if day == 0:
                st.write("")
            else:
                date_obj = dt.date(year, month, day)
                selected_date = st.session_state.selected_date

                label = f"{day}"
                if date_obj == selected_date:
                    label = f"[{label}]"
                if day in days_with_events:
                    label = f"{label} ●"

                if st.button(label, key=f"day-{year}-{month}-{day}"):
                    st.session_state.selected_date = date_obj

st.write("---")

# ==================== 아래: 새 일정 입력 ====================
st.markdown("#### 새 일정 입력")

selected_date = st.session_state.selected_date
st.write(f"선택한 날짜: **{selected_date}**")

c1, c2, c3, c4 = st.columns(4)

with c1:
    title = st.text_input("일정명", key="title", placeholder="예: 수학 학원")

with c2:
    st.markdown('<div class="pill-input">', unsafe_allow_html=True)
    place = st.text_input("장소", key="place", placeholder="예: OO학원")
    st.markdown('</div>', unsafe_allow_html=True)

with c3:
    start_time = st.time_input("시작시간", value=dt.time(18, 0))

with c4:
    end_time = st.time_input("종료시간", value=dt.time(19, 0))

st.write("")

btn_col = st.columns([1, 2, 1])[1]
with btn_col:
    clicked = st.button(
        "입력",
        key="submit",
        disabled=not st.session_state.logged_in,
        help="구글 로그인 후 사용 가능합니다.",
    )

if clicked and st.session_state.logged_in:
    st.success(
        f"새 일정이 준비되었습니다: "
        f"{selected_date} {start_time.strftime('%H:%M')}~{end_time.strftime('%H:%M')} "
        f"/ {title} @ {place}"
    )
    # TODO: 여기서 기존 일정 + 교통/동선 체크 → OK면 캘린더에 insert
