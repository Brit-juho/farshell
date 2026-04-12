# TTS 고급 설정 구현 계획

## 개요
SettingsScreen에서 진입 가능한 **AdvancedTTSSettingsScreen**을 새로 만들어, 시간 알림 멘트 / 부재중 통화 멘트를 세밀하게 커스텀하고 테스트할 수 있게 한다.

---

## 수정 대상 파일 (8개)

| # | 파일 | 작업 |
|---|------|------|
| 1 | `data/model/MissedCallAnnouncementStyle.kt` | **신규** - 따로/같이 enum |
| 2 | `data/preferences/SettingsDataStore.kt` | 8개 신규 설정 key 추가 |
| 3 | `ui/viewmodel/SettingsViewModel.kt` | UiState 필드 추가, 3번째 combine, setter, 테스트 메서드 |
| 4 | `util/TTSManager.kt` | `speakTimeFormatted()`, 합산 멘트 메서드 추가, 샘플 상수 제거 |
| 5 | `service/HourlyAlarmService.kt` | 하드코딩 값을 DataStore 설정으로 교체 |
| 6 | `ui/screens/AdvancedTTSSettingsScreen.kt` | **신규** - 고급 TTS 설정 화면 |
| 7 | `ui/screens/SettingsScreen.kt` | 고급 설정 진입 SettingRow 추가 |
| 8 | `ui/screens/VoiceSelectScreen.kt` | SAMPLE_TIME_TEXT → 동적 생성 |
| 9 | `MainActivity.kt` | 네비게이션 라우트 추가 |
| 10 | `res/values/strings.xml` | 신규 문자열 리소스 |

---

## Step 1: MissedCallAnnouncementStyle enum 생성

**신규 파일**: `data/model/MissedCallAnnouncementStyle.kt`

```kotlin
enum class MissedCallAnnouncementStyle {
    SEPARATE,  // 따로: "홍길동님에게서 부재중 전화가 3건 왔습니다"
    COMBINED;  // 같이: "홍길동님께 1건, 김철수님께 2건 왔습니다"

    companion object {
        fun fromName(name: String) = entries.find { it.name == name } ?: SEPARATE
    }
}
```

---

## Step 2: SettingsDataStore에 8개 설정 추가

**파일**: `SettingsDataStore.kt`

새로운 키 & Flow/setter 추가:

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `tts_time_format_24h` | Boolean | `false` | 24시간제 ON/OFF |
| `tts_custom_time_template` | String | `"지금은 %s %d시입니다"` | 사용자 커스텀 멘트 |
| `tts_repeat_interval_ms` | Int | `2000` | 반복 사이 간격(ms) |
| `tts_missed_call_total_count` | Boolean | `true` | 총 건수 안내 ON/OFF |
| `tts_missed_call_individual` | Boolean | `true` | 개별 안내 ON/OFF |
| `tts_missed_call_max_people` | Int | `3` | 개별 안내 최대 N명 |
| `tts_missed_call_style` | String | `"SEPARATE"` | 따로/같이 |
| `tts_missed_call_overflow_msg` | Boolean | `true` | N명 초과시 메시지 ON/OFF |

+ `DEFAULT_TIME_TEMPLATE` 상수 추가

---

## Step 3: SettingsUiState & SettingsViewModel 확장

**파일**: `SettingsViewModel.kt`

### UiState 필드 추가
```kotlin
data class SettingsUiState(
    // 기존 필드 유지...

    // 고급 TTS 설정 (신규)
    val ttsTimeFormat24h: Boolean = false,
    val ttsCustomTimeTemplate: String = "지금은 %s %d시입니다",
    val ttsRepeatIntervalMs: Int = 2000,
    val ttsMissedCallTotalCount: Boolean = true,
    val ttsMissedCallIndividual: Boolean = true,
    val ttsMissedCallMaxPeople: Int = 3,
    val ttsMissedCallStyle: MissedCallAnnouncementStyle = MissedCallAnnouncementStyle.SEPARATE,
    val ttsMissedCallOverflowMsg: Boolean = true,
)
```

### 3번째 combine() 추가
기존 combine이 9개 파라미터(Kotlin 최대)이므로, 테마/폰트 패턴처럼 별도 collector 추가.

### setter 8개 추가
기존 패턴과 동일 (`viewModelScope.launch { settingsDataStore.setXxx() }`)

### testAlarm() 수정
- `speakTime(hour)` → `speakTimeFormatted(hour, use24h, customTemplate)` 호출

### testMissedCallAnnouncement() 신규
- 설정된 `maxPeople` 수만큼 가짜 데이터 생성
  - 이름 목록: 홍길동, 김철수, 이영희, 박민수, 최수진 ...
  - 각각 1건, 2건, 3건 ... N건
- 현재 설정(총건수 ON/OFF, 개별 ON/OFF, 따로/같이, 초과 ON/OFF)에 맞게 TTS 재생
- 초과 안내 테스트용: 항상 "그 외 2명" 으로 시뮬레이션

---

## Step 4: TTSManager 수정

**파일**: `TTSManager.kt`

### speakTimeFormatted() 추가
```kotlin
fun speakTimeFormatted(
    hour: Int,
    use24h: Boolean,
    customTemplate: String,
    onComplete: (() -> Unit)? = null
) {
    val (amPmStr, displayHour) = if (use24h) {
        "" to hour
    } else {
        val amPm = context.getString(if (hour < 12) R.string.am else R.string.pm)
        val dh = when {
            hour == 0 -> 12
            hour > 12 -> hour - 12
            else -> hour
        }
        amPm to dh
    }
    val text = try {
        String.format(customTemplate, amPmStr, displayHour)
            .replace("  ", " ").trim()
    } catch (e: Exception) {
        // 잘못된 템플릿 폴백
        context.getString(R.string.tts_time_format, amPmStr, displayHour)
    }
    speak(text, onComplete)
}
```

### speakCombinedMissedCalls() 추가
```kotlin
fun speakCombinedMissedCalls(
    grouped: List<Pair<String, Int>>,
    onComplete: (() -> Unit)? = null
) {
    // "홍길동님께 1건, 김철수님께 2건 왔습니다"
    val parts = grouped.joinToString(", ") { "${it.first}님께 ${it.second}건" }
    speak("$parts 왔습니다", onComplete)
}
```

### SAMPLE_TIME_TEXT / SAMPLE_CALL_TEXT 제거
companion object에서 삭제

---

## Step 5: HourlyAlarmService 수정

**파일**: `HourlyAlarmService.kt`

### playHourlyAlarm()에서 신규 설정 읽기
```kotlin
val timeFormat24h = settingsDataStore.ttsTimeFormat24h.first()
val customTimeTemplate = settingsDataStore.ttsCustomTimeTemplate.first()
val repeatIntervalMs = settingsDataStore.ttsRepeatIntervalMs.first()
val missedCallTotalCount = settingsDataStore.ttsMissedCallTotalCount.first()
val missedCallIndividual = settingsDataStore.ttsMissedCallIndividual.first()
val missedCallMaxPeople = settingsDataStore.ttsMissedCallMaxPeople.first()
val missedCallStyleStr = settingsDataStore.ttsMissedCallStyle.first()
val missedCallOverflowMsg = settingsDataStore.ttsMissedCallOverflowMsg.first()
```

### playWithTTS() 변경
- `speakTime(hour)` → `speakTimeFormatted(hour, timeFormat24h, customTimeTemplate)`
- `delay(2000)` → `delay(repeatIntervalMs.toLong())`

### announceMissedCalls() 변경
- 총 건수: `if (totalCountEnabled)` 로 감싸기
- 개별 안내: `if (individualEnabled)` 로 감싸기
- `take(3)` → `take(maxPeople)`
- SEPARATE / COMBINED 분기 추가
- 초과 메시지: `if (overflowMsgEnabled && grouped.size > maxPeople)` 로 감싸기

---

## Step 6: strings.xml 추가

```xml
<!-- TTS 고급 설정 -->
<string name="advanced_tts_settings">TTS 멘트 설정</string>
<string name="time_announcement_settings">시간 알림 멘트</string>
<string name="missed_call_announcement_settings">부재중 통화 멘트</string>
<string name="use_24h_format">24시간 형식</string>
<string name="format_12h_example">오전/오후 3시</string>
<string name="format_24h_example">15시</string>
<string name="message_template">멘트 템플릿</string>
<string name="template_help">%s = 오전/오후, %d = 시간</string>
<string name="reset_to_default">기본값으로 돌리기</string>
<string name="repeat_interval_label">반복 간격</string>
<string name="seconds">초</string>
<string name="total_count_announcement">총 건수 안내</string>
<string name="total_count_desc">부재중 전화가 N건 있습니다</string>
<string name="individual_announcement">개별 안내</string>
<string name="individual_announcement_desc">개인별 부재중 전화 안내</string>
<string name="max_people_count">최대 안내 인원</string>
<string name="people_suffix">명</string>
<string name="announcement_style">안내 방식</string>
<string name="style_separate">따로</string>
<string name="style_combined">같이</string>
<string name="style_separate_example">홍길동님에게서 부재중 전화가 3건 왔습니다</string>
<string name="style_combined_example">홍길동님께 1건, 김철수님께 2건 왔습니다</string>
<string name="overflow_message">초과 인원 안내</string>
<string name="overflow_message_desc">그 외 N명에게서 더 왔습니다</string>
<string name="test_time_tts">시간 알림 테스트</string>
<string name="test_missed_call_tts">부재중 통화 테스트</string>
```

---

## Step 7: AdvancedTTSSettingsScreen 신규 생성

**신규 파일**: `ui/screens/AdvancedTTSSettingsScreen.kt`

### 화면 구조

```
TopAppBar: "TTS 멘트 설정" + 뒤로가기

[ScrollableColumn]

━━━ 시간 알림 멘트 ━━━

  [Switch] 24시간 형식
    설명: "OFF: 오전/오후 3시 → ON: 15시"

  [OutlinedTextField] 멘트 템플릿
    기본값: "지금은 %s %d시입니다"
    도움말: "%s = 오전/오후, %d = 시간"
  [OutlinedButton] 기본값으로 돌리기

  [미리보기] 현재 시각 기준으로 렌더링된 텍스트 표시

  [NumberInput] 반복 간격: ___초 (range: 1~10, 내부적으로 ×1000 → ms)

  [Button] 시간 알림 테스트 ▶ / 중지 ■

━━━ 부재중 통화 멘트 ━━━

  [Switch] 총 건수 안내
    설명: "부재중 전화가 N건 있습니다"

  [Switch] 개별 안내
    설명: "개인별 부재중 전화 안내"

  (개별 안내 ON일 때만 표시)
    [NumberInput] 최대 안내 인원: ___명 (range: 1~10)
    [Chips] 안내 방식: [따로] [같이]
      따로 예시: "홍길동님에게서 부재중 전화가 3건 왔습니다"
      같이 예시: "홍길동님께 1건, 김철수님께 2건 왔습니다"

  [Switch] 초과 인원 안내
    설명: "그 외 N명에게서 더 왔습니다"

  [Button] 부재중 통화 테스트 ▶ / 중지 ■
```

### 테스트 동작
- **시간 알림 테스트**: 현재 시각 + 설정된 포맷/템플릿으로 TTS 재생
- **부재중 통화 테스트**:
  - 설정된 maxPeople 수만큼 가짜 데이터 생성
  - 이름: 홍길동(1건), 김철수(2건), 이영희(3건) ...
  - 설정된 ON/OFF, 따로/같이에 맞게 순서대로 재생
  - 초과 안내 ON이면 "그 외 2명에게서 더 왔습니다" 추가

---

## Step 8: SettingsScreen 수정

**파일**: `SettingsScreen.kt`

- `onNavigateToAdvancedTTS: () -> Unit` 파라미터 추가
- "정각 알림" 섹션(반복 횟수 아래)에 진입 SettingRow 추가:
  ```kotlin
  SettingRow(
      label = "TTS 멘트 설정",
      value = "",
      icon = Icons.Filled.TextFields,
      onClick = onNavigateToAdvancedTTS
  )
  ```

---

## Step 9: VoiceSelectScreen 수정

**파일**: `VoiceSelectScreen.kt`

- `TTSManager.SAMPLE_TIME_TEXT` 참조 제거
- 동적으로 현재 시각 + 고급 설정 사용:
  ```kotlin
  val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
  ttsManager.speakTimeFormatted(hour, uiState.ttsTimeFormat24h, uiState.ttsCustomTimeTemplate)
  ```

---

## Step 10: MainActivity 네비게이션 추가

**파일**: `MainActivity.kt`

```kotlin
composable("advanced_tts_settings") {
    AdvancedTTSSettingsScreen(
        viewModel = settingsViewModel,
        onNavigateBack = { navController.popBackStack() }
    )
}
```

SettingsScreen 호출부에 `onNavigateToAdvancedTTS` 콜백 추가.

---

## 구현 순서

1. **Step 1** - MissedCallAnnouncementStyle enum (의존성 없음)
2. **Step 2** - SettingsDataStore (Step 1에 의존)
3. **Step 4** - TTSManager (의존성 없음, 먼저 해도 됨)
4. **Step 6** - strings.xml (의존성 없음)
5. **Step 3** - SettingsViewModel (Step 1,2에 의존)
6. **Step 5** - HourlyAlarmService (Step 2,3,4에 의존)
7. **Step 7** - AdvancedTTSSettingsScreen (Step 3,6에 의존)
8. **Step 8** - SettingsScreen 수정 (Step 7에 의존)
9. **Step 9** - VoiceSelectScreen 수정 (Step 4에 의존)
10. **Step 10** - MainActivity 네비게이션 (Step 7,8에 의존)

---

## 주의사항

- **템플릿 안전성**: `String.format()` 실패 시 기본 포맷으로 폴백 (try-catch)
- **24시간 모드 공백**: `%s`가 빈 문자열이 되면 이중 공백 발생 → `.replace("  ", " ").trim()` 처리
- **combine 9개 제한**: 기존 패턴처럼 별도 coroutine에서 3번째 combine 사용
- **반복 간격**: UI에서는 "초" 단위(1~10), 내부 저장은 ms 단위(×1000)
