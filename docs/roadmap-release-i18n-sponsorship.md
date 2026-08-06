# Release, language and sponsorship roadmap

## Versioned updates

- Publish semantic versions such as `1.0.0-rc.3`, `1.0.0`, `1.1.0`.
- Create matching Git tags and GitHub Releases so HACS displays version numbers instead of commit hashes.
- Maintain localized release notes with sections for new features, fixes, migrations and known limitations.
- Expose installed version, available version and release notes in FRAKON Energy Settings.

## Documentation

- Expand README with product overview, installation, setup, screenshots, supported technologies, data flow, privacy, troubleshooting and upgrade instructions.
- Add a visual gallery for desktop, tablet and mobile.
- Document how existing Home Assistant entities are reused and when FRAKON creates derived entities.

## Sponsorship

- Add optional sponsorship links such as Buy Me a Coffee, GitHub Sponsors or another configured provider.
- Show a non-blocking, dismissible support suggestion only after at least seven days of successful use.
- Never interrupt setup, hide functionality, repeatedly nag, or show the prompt when disabled.
- Store dismissal and last-shown timestamps locally.

## Internationalization

- Detect the active Home Assistant user language by default.
- Allow a manual language override in FRAKON Energy Settings.
- Fall back to English for missing translations.
- Localize frontend text, errors, release notes, technology names, units and date/number formatting.
- Initial languages: Czech and English; architecture must support additional locale files without code changes.
