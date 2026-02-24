# WIGVO — ACL 2026 System Demonstrations Paper

## Files

| File | Description |
|------|-------------|
| `acl2026_wigvo.tex` | Main paper (6 pages + references) |
| `references.bib` | Bibliography |
| `figures/` | Figures directory (to be added) |

## Required Figures

논문에서 참조하는 figure 파일들:

1. `figures/architecture.tikz` — 시스템 아키텍처 다이어그램 (TikZ, 컴파일 시 자동 생성)
2. `figures/screenshot_call.png` — 통화 중 웹 UI 스크린샷

## Build

```bash
cd docs/paper
pdflatex acl2026_wigvo
bibtex acl2026_wigvo
pdflatex acl2026_wigvo
pdflatex acl2026_wigvo
```

## Submission Checklist

- [ ] Paper: 6 pages max (excluding ethics + references)
- [ ] Demo video: max 2.5 minutes (YouTube or MPEG4)
- [ ] Live demo URL: https://wigvo.run
- [ ] Figures: architecture diagram + UI screenshot
- [ ] OpenReview submission
- [ ] Deadline: February 27, 2026, 23:59 UTC-12

## Key Dates

| Event | Date |
|-------|------|
| Submission deadline | Feb 27, 2026 |
| Notification | Apr 24, 2026 |
| Camera-ready | May 15, 2026 |
| Conference | Jul 2-7, 2026 (Vienna) |
