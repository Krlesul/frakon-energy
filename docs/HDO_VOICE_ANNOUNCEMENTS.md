# HDO countdown and voice announcements

FRAKON Energy exposes the current tariff and a live `HH:MM:SS` countdown to the next switch. The countdown sensor is intended for the main dashboard card next to the current NT/VT state.

When a real tariff transition occurs, the integration emits this Home Assistant event:

```text
frakon_energy_tariff_changed
```

Event payload example:

```yaml
source_id: "402D25H1700000000001|1"
source_name: "ČEZ HDO"
previous_tariff: "VT"
new_tariff: "NT"
low_tariff_active: true
changed_at: "2026-08-04T21:35:00+02:00"
next_change_at: "2026-08-04T23:50:00+02:00"
next_change_in_seconds: 8100
```

The first coordinator refresh never emits an event. Unknown and unavailable source states are ignored, preventing false announcements after a restart or temporary source outage.

## Assist satellite announcement

```yaml
alias: FRAKON Energy - oznámení změny HDO
triggers:
  - trigger: event
    event_type: frakon_energy_tariff_changed
conditions:
  - condition: time
    after: "07:00:00"
    before: "22:00:00"
actions:
  - action: assist_satellite.announce
    target:
      entity_id: assist_satellite.kuchyne
    data:
      message: >-
        {% if trigger.event.data.new_tariff == 'NT' %}
          Byl aktivován nízký tarif.
        {% else %}
          Nízký tarif byl ukončen. Nyní je aktivní vysoký tarif.
        {% endif %}
mode: queued
```

## Media player TTS announcement

```yaml
alias: FRAKON Energy - HDO přes reproduktor
triggers:
  - trigger: event
    event_type: frakon_energy_tariff_changed
actions:
  - action: tts.speak
    target:
      entity_id: tts.home_assistant_cloud
    data:
      media_player_entity_id: media_player.obyvaci_pokoj
      message: >-
        {% if trigger.event.data.new_tariff == 'NT' %}
          Byl aktivován nízký tarif.
        {% else %}
          Nízký tarif skončil. Nyní je aktivní vysoký tarif.
        {% endif %}
mode: queued
```

Voice announcements remain opt-in. FRAKON Energy only provides the reliable transition event; the user chooses speakers, quiet hours, volume and wording in Home Assistant automation settings.
