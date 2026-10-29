# Re-recording the demo

`docs/demo.gif` is a scripted walkthrough recorded in real Chrome (a cited answer, a paraphrased attack caught by the ML layer,
PII redaction, a refusal, and the audit ledger). Re-record it after UI changes:

```bash
cd frontend
npm run build
# Serve the build against a backend that holds only the sample policies, so no personal documents appear on screen.
VITE_API_PROXY=http://localhost:8000 npx vite preview --port 5174 &
E2E_BASE_URL=http://localhost:5174 E2E_ADMIN_KEY=<your admin key> node demo/record.mjs
ffmpeg -y -i demo-out/walkthrough.webm   -vf "fps=5,scale=760:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=64:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"   ../docs/demo.gif
```

The admin key is typed into a masked password field, so it is not visible in the recording. Check the archive list in the first frame
before committing: whatever documents are in the database appear in the GIF.
