## Inspiration

Heat is the deadliest weather hazard in the US, and on job sites it's badly undercounted. The person overheating usually can't tell. By the time you feel dizzy, your core temperature is already high. Most heat deaths at work happen in a worker's first few days, before their body adapts, and new workers are the ones most likely to push through instead of speaking up.

Florida crews at solar farms, substations and power plants work in this every summer, with a thermometer on the wall and a supervisor's gut feeling. We wanted something that sees it coming.

**Redline** is a wristband that warns workers 15+ minutes before they overheat, plans the crew's day around the heat, and calls for help when someone goes down. One rule runs through all of it: **AI predicts and suggests, plain code makes every safety decision.**

## How we built it

**The band.** An ESP32-S3 Feather on the forearm with an accelerometer, a diode skin thermometer, a buzzer, a button and a status light. Our own Arduino firmware streams everything to a laptop over Bluetooth LE, no WiFi needed, and catches falls by reading the accelerometer 400 times a second.

**Core temperature now.** The US Army's ECTemp Kalman filter estimates core temperature $CT$ from heart rate every minute:

$$K = \frac{v^- c}{c^2 v^- + \sigma^2}, \qquad CT = CT^- + K\,\big(HR - h(CT^-)\big)$$

where $h(CT)$ is the expected heart rate at that core temperature.

**The 30-minute forecast.** A GRU neural network reads the last hour minute by minute and predicts a low / likely / high range for the next 30 minutes. We pretrained it on 9,000 simulated shifts from the ISO 7933 heat-strain model, fine-tuned on 65 real people from a published study (core temperature pills), and locked 10 of them away to score exactly once. Its loss punishes underestimating a hot worker extra:

$$\mathcal{L} = \underbrace{\sum_q \max\big(q\,e,\,(q-1)\,e\big)}_{\text{quantile loss}} + \lambda\, w(y)\,\max(0,\, y-\hat{y})^2$$

Five networks vote on every forecast, and conformal calibration makes "90% sure" actually hold 90% of the time.

**The planner.** A crew's day has about $10^{107}$ possible schedules. Google's OR-Tools solver finds the most productive one that keeps everyone's predicted temperature under the warning line. Claude explains it in plain words, and a code rule checker approves every plan. The agent also improves itself: it invents practice days, keeps a playbook of lessons, and evolves its strategy, keeping a change only if it wins on days it never saw.

**Offline AI.** We distilled Claude into a 1 GB open-source model (Qwen3-1.7B + LoRA, trained on a MacBook) so the planner works on sites with no internet.

**Hazards and the call.** Live satellite lightning, flood gauges and weather warnings for 79 real Florida plants, plus a model trained on 7 years of warnings that predicts tomorrow's risk 2-4× better than normal rates. On a hard fall with no button press in 20 seconds, an ElevenLabs voice agent calls and reads out who and where. In the demo it calls a teammate, never 911.

**Results** (on people and days the models never saw): forecast error 0.370 °C at 20 minutes vs 0.481 for the Army method (23% better); 88% of heat warnings caught 16.9 minutes early; AI-only plans broke safety rules 92% of the time on simulated days, ours 0%.

## Challenges we ran into

- **No parts could be ordered.** No Grove cables existed at the hackathon, so we rebuilt the band around a Prop-Maker FeatherWing and a plain photoresistor.
- **No pulse sensor.** We tried reading a pulse through a fingertip with the photoresistor and a phone flashlight. It caught a real 53 bpm, then a perfectly steady fake 90 bpm appeared: the flashlight's invisible flicker, sampled too slowly. We fixed the aliasing in firmware, but the signal stayed too weak, so in the live demo heart rate is a clearly labeled demo input.
- **Our first fall test failed.** The drop was detected, then sensor wobble kept cancelling the alarm. Now a real fall locks in and only the button can stop the call.
- **AI alone wasn't safe.** That's why code approves every plan.
- **Numbers that looked too good.** Our model scored 0.230 on the people we used to pick it and 0.370 on the locked test. We report 0.370.

## What we learned

- **Measure before you believe.** Learning each worker from heart rate made forecasts worse; a research-backed break schedule didn't hold up in our planner. Finding out why led to better ideas.
- **Report where you don't win.** A simple "temperature stays flat, with an honest range" baseline warned almost as early as our network (16.1 vs 16.9 min). Most of the early warning comes from honest uncertainty; our model adds accuracy.
- **Draw a hard line between AI and safety.** Let models predict and explain, and keep every alert and emergency decision in plain, tested code.
- **Hardware humbles you.** A real compile, a real upload and a real drop test found bugs no simulation did.

Full write-up, results and code: [github.com/aidenkaro10/redline](https://github.com/aidenkaro10/redline)
