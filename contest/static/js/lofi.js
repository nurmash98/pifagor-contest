/* Бесконечная «рабочая» lo-fi музыка, которая генерируется прямо в браузере (Web Audio API).
   Никаких аудиофайлов и чужих треков: аккорды электропиано, бас, мягкие барабаны, редкая мелодия и шум винила.
   Каждые 8 тактов меняется последовательность аккордов, мелодия каждый раз новая — музыка не повторяется.
   Использование: LoFi.start(); LoFi.stop(); LoFi.setVolume(0..1); LoFi.playing */
(function () {
    'use strict';
    var ctx = null, master = null, warm = null, crackle = null, timer = null, noiseBuf = null, analyser = null;
    var BPM = 76, beat = 60 / BPM, step = beat / 2;          // восьмые
    var nextTime = 0, stepIdx = 0, bar = 0, progIdx = 0, volume = 0.5;
    var A4 = 440;
    function hz(m) { return A4 * Math.pow(2, (m - 69) / 12); }
    // последовательности (MIDI-ноты аккордов, бас = первая нота - 12)
    var PROGS = [
        [[50, 53, 57, 60, 64], [55, 59, 62, 65, 69], [48, 52, 55, 59, 62], [57, 60, 64, 67, 71]],   // Dm9 G13 Cmaj9 Am9
        [[53, 57, 60, 64, 67], [52, 55, 59, 62, 66], [50, 53, 57, 60, 64], [48, 52, 55, 59, 62]],   // Fmaj9 Em9 Dm9 Cmaj9
        [[45, 48, 52, 55, 59], [50, 53, 57, 60, 62], [55, 59, 62, 65, 69], [48, 52, 55, 59, 64]],   // Am9 Dm9 G13 Cmaj9
        [[48, 52, 55, 59, 62], [57, 60, 64, 67, 71], [53, 57, 60, 64, 67], [55, 59, 62, 65, 69]]    // Cmaj9 Am9 Fmaj9 G13
    ];
    var PENTA = [0, 2, 4, 7, 9];

    function noise() {
        if (noiseBuf) { return noiseBuf; }
        noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
        var d = noiseBuf.getChannelData(0);
        for (var i = 0; i < d.length; i++) { d[i] = Math.random() * 2 - 1; }
        return noiseBuf;
    }
    function env(g, t, a, peak, dec) {
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(peak, t + a);
        g.gain.exponentialRampToValueAtTime(0.0001, t + a + dec);
    }
    function epiano(m, t, len, vel) {                       // электропиано: синус + треугольник с лёгкой расстройкой
        [['sine', 0, 1], ['triangle', 7, .35], ['sine', -5, .25]].forEach(function (o) {
            var osc = ctx.createOscillator(), g = ctx.createGain();
            osc.type = o[0]; osc.frequency.value = hz(m); osc.detune.value = o[1];
            env(g, t, .015, .06 * vel * o[2], len);
            osc.connect(g); g.connect(warm); osc.start(t); osc.stop(t + len + .1);
        });
    }
    function bass(m, t, len) {
        var osc = ctx.createOscillator(), g = ctx.createGain();
        osc.type = 'sine'; osc.frequency.value = hz(m - 12);
        env(g, t, .02, .22, len);
        osc.connect(g); g.connect(warm); osc.start(t); osc.stop(t + len + .1);
    }
    function kick(t) {
        var osc = ctx.createOscillator(), g = ctx.createGain();
        osc.frequency.setValueAtTime(120, t); osc.frequency.exponentialRampToValueAtTime(42, t + .18);
        env(g, t, .005, .55, .28);
        osc.connect(g); g.connect(master); osc.start(t); osc.stop(t + .4);
    }
    function snare(t) {
        var src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
        src.buffer = noise(); f.type = 'bandpass'; f.frequency.value = 1800; f.Q.value = .8;
        env(g, t, .003, .16, .16);
        src.connect(f); f.connect(g); g.connect(warm); src.start(t, Math.random()); src.stop(t + .25);
    }
    function hat(t, v) {
        var src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
        src.buffer = noise(); f.type = 'highpass'; f.frequency.value = 7000;
        env(g, t, .002, .045 * v, .05);
        src.connect(f); f.connect(g); g.connect(master); src.start(t, Math.random()); src.stop(t + .08);
    }
    function lead(m, t, len) {                              // мягкая мелодия
        var osc = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
        osc.type = 'square'; osc.frequency.value = hz(m); f.type = 'lowpass'; f.frequency.value = 1200;
        env(g, t, .03, .025, len);
        osc.connect(f); f.connect(g); g.connect(warm); osc.start(t); osc.stop(t + len + .1);
    }
    function startCrackle() {                               // треск винила: редкие щелчки + очень тихое шипение
        var src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
        var buf = ctx.createBuffer(1, ctx.sampleRate * 4, ctx.sampleRate), d = buf.getChannelData(0);
        for (var i = 0; i < d.length; i++) {
            d[i] = (Math.random() * 2 - 1) * .04;
            if (Math.random() < 0.0004) { d[i] = (Math.random() < .5 ? -1 : 1) * (.3 + Math.random() * .7); }
        }
        src.buffer = buf; src.loop = true; f.type = 'bandpass'; f.frequency.value = 3000; f.Q.value = .5; g.gain.value = .05;
        src.connect(f); f.connect(g); g.connect(master); src.start();
        crackle = src;
    }

    function schedule() {
        while (nextTime < ctx.currentTime + 0.6) {
            var prog = PROGS[progIdx], chord = prog[bar % 4], s = stepIdx % 8;
            var swing = (s % 2) ? step * .18 : 0, t = nextTime + swing;
            if (s === 0) {
                chord.forEach(function (m, i) { epiano(m, t + i * .012, beat * 3.6, .9); });
                bass(chord[0], t, beat * 1.6);
                kick(t);
            }
            if (s === 4) { bass(chord[0] + (Math.random() < .4 ? 7 : 0), t, beat * 1.4); if (Math.random() < .6) { kick(t); } }
            if (s === 5 && Math.random() < .45) { epiano(chord[2] + 12, t, beat * 1.2, .5); epiano(chord[4], t, beat * 1.2, .45); }
            if (s === 2 || s === 6) { snare(t); }
            if (s === 7 && Math.random() < .3) { kick(t); }
            hat(t, s % 2 ? .6 : 1);
            if (Math.random() < .22 && s !== 0) {                // редкая мелодия по пентатонике от тоники аккорда
                var root = chord[0] + 12, n = root + PENTA[Math.floor(Math.random() * PENTA.length)] + (Math.random() < .3 ? 12 : 0);
                lead(n, t, step * (1 + Math.floor(Math.random() * 3)));
            }
            stepIdx++;
            if (stepIdx % 8 === 0) {
                bar++;
                if (bar % 8 === 0) { progIdx = (progIdx + 1 + Math.floor(Math.random() * (PROGS.length - 1))) % PROGS.length; }
            }
            nextTime += step;
        }
    }

    window.LoFi = {
        playing: false,
        start: function () {
            try {
                if (!ctx) {
                    ctx = new (window.AudioContext || window.webkitAudioContext)();
                    master = ctx.createGain(); master.gain.value = volume * .6; master.connect(ctx.destination);
                    warm = ctx.createBiquadFilter(); warm.type = 'lowpass'; warm.frequency.value = 2600; warm.Q.value = .3; warm.connect(master);
                    analyser = ctx.createAnalyser(); analyser.fftSize = 2048; master.connect(analyser);
                    startCrackle();
                }
                if (ctx.state === 'suspended') { ctx.resume(); }
                if (!timer) { nextTime = ctx.currentTime + .1; timer = setInterval(schedule, 100); }
                this.playing = true;
            } catch (e) { this.playing = false; }
        },
        stop: function () {
            if (timer) { clearInterval(timer); timer = null; }
            if (ctx) { ctx.suspend(); }
            this.playing = false;
        },
        level: function () {                                  // громкость сигнала сейчас (для проверки)
            if (!analyser) { return 0; }
            var a = new Float32Array(analyser.fftSize), s = 0; analyser.getFloatTimeDomainData(a);
            for (var i = 0; i < a.length; i++) { s += a[i] * a[i]; }
            return Math.sqrt(s / a.length);
        },
        setVolume: function (v) { volume = Math.max(0, Math.min(1, v)); if (master) { master.gain.value = volume * .6; } }
    };
})();
