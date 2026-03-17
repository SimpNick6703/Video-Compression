document.addEventListener('DOMContentLoaded', () => {
    // --- Theme Toggle Logic ---
    const themeBtn = document.getElementById('theme-toggle');
    const sunIcon = document.getElementById('icon-sun');
    const moonIcon = document.getElementById('icon-moon');
    let encodeChart = null;

    function toggleTheme() {
        document.body.classList.toggle('light-mode');
        const isLight = document.body.classList.contains('light-mode');
        
        // Icon visibility swap
        if(isLight) {
            sunIcon.classList.remove('hidden');
            moonIcon.classList.add('hidden');
        } else {
            sunIcon.classList.add('hidden');
            moonIcon.classList.remove('hidden');
        }

        // Update Chart Colors
        if(encodeChart) {
            Chart.defaults.color = isLight ? '#475569' : '#94a3b8';
            Chart.defaults.borderColor = isLight ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
            encodeChart.update();
        }
    }

    if(themeBtn) {
        themeBtn.addEventListener('click', toggleTheme);
    }

    // --- Navigation Logic ---
    const navBtns = document.querySelectorAll('.nav-btn');
    const sections = document.querySelectorAll('.content-section');

    navBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            navBtns.forEach(b => {
                b.classList.remove('tab-active');
                b.classList.add('text-slate-400');
            });
            btn.classList.add('tab-active');
            btn.classList.remove('text-slate-400');

            sections.forEach(sec => sec.classList.add('hidden-section'));
            const target = document.getElementById(btn.dataset.target);
            if(target) target.classList.remove('hidden-section');
        });
    });

    // --- Chart.js Initialization ---
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = "'JetBrains Mono', monospace";
    
    const ctx = document.getElementById('encodeChart').getContext('2d');
    encodeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Serial', 'Parallel'],
            datasets: [
                { label: 'Chunk 1', data: [240, 60], backgroundColor: '#06b6d4', barThickness: 40 },
                { label: 'Chunk 2', data: [0, 60], backgroundColor: '#8b5cf6', barThickness: 40 },
                { label: 'Stitch Overhead', data: [0, 5], backgroundColor: '#d946ef', barThickness: 40 }
            ]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: { callbacks: { label: (c) => c.dataset.label } }
            },
            scales: {
                x: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { display: false } },
                y: { stacked: true, grid: { display: false } }
            }
        }
    });

    // --- BPP Calculator Logic ---
    const MIN_BPP = 0.04;
    const MB_TO_BITS = 8 * 1024 * 1024;

    function calculateBPP() {
        const targetMB = parseFloat(document.getElementById('calc-mb').value) || 100;
        const duration = parseFloat(document.getElementById('calc-dur').value) || 120;
        const srcW = parseInt(document.getElementById('calc-w').value) || 1920;
        const srcH = parseInt(document.getElementById('calc-h').value) || 1080;
        const srcFps = parseFloat(document.getElementById('calc-fps').value) || 60;

        const targetBits = targetMB * MB_TO_BITS;
        const aspectRatio = srcW / srcH;

        // Filter Options
        let heightOptions = [2160, 1440, 1080, 720];
        let fpsOptions = [120.0, 90.0, 60.0];

        let validHeights = heightOptions.filter(h => h <= srcH);
        if (!validHeights.includes(srcH)) validHeights.unshift(srcH);

        let validFps = fpsOptions.filter(f => f <= srcFps);
        if (!validFps.includes(srcFps)) validFps.unshift(srcFps);

        let candidates = [];
        validHeights.forEach(h => {
            const w = Math.round(h * aspectRatio);
            validFps.forEach(f => {
                const pixelsPerSec = w * h * f;
                const bpp = targetBits / (duration * pixelsPerSec);
                candidates.push({ h, w, f, bpp, pps: pixelsPerSec, fpsPriority: f >= 60 });
            });
        });

        // Sort logic: Top candidates > MIN_BPP, then FPS >= 60, then throughput
        let safeOnes = candidates.filter(c => c.bpp >= MIN_BPP);
        safeOnes.sort((a, b) => {
            if (a.fpsPriority !== b.fpsPriority) return a.fpsPriority ? -1 : 1;
            return b.pps - a.pps;
        });

        const best = safeOnes.length > 0 ? safeOnes[0] : candidates[candidates.length - 1]; // Fallback to smallest

        // Render Results
        const tbody = document.getElementById('calc-results');
        if(tbody) {
            tbody.innerHTML = '';
            candidates.forEach(c => {
                const isBest = c === best;
                const isSafe = c.bpp >= MIN_BPP;
                
                const tr = document.createElement('tr');
                if (isBest) tr.className = 'bg-cyan-900/30 border-l-2 border-cyan-400';
                tr.innerHTML = `
                    <td class="px-4 py-3 ${isBest ? 'text-cyan-400 font-bold' : ''}">${c.w}x${c.h}</td>
                    <td class="px-4 py-3">${c.f}</td>
                    <td class="px-4 py-3 ${isSafe ? 'text-emerald-400' : 'text-rose-400'}">${c.bpp.toFixed(4)}</td>
                    <td class="px-4 py-3 uppercase text-[10px]">${isSafe ? '<span class="text-emerald-400">Pass</span>' : '<span class="text-rose-400">Fail</span>'}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        const conc = document.getElementById('calc-conclusion');
        if(conc) {
            conc.classList.remove('hidden', 'border-emerald-500', 'bg-emerald-900/20', 'border-amber-500', 'bg-amber-900/20');
            if (best.bpp >= MIN_BPP) {
                conc.classList.add('border-emerald-500', 'bg-emerald-900/20');
                conc.innerHTML = `<strong>Quality Pass:</strong> Script will use <strong>${best.w}x${best.h} @ ${best.f}fps</strong>. Clarity threshold maintained.`;
            } else {
                conc.classList.add('border-amber-500', 'bg-amber-900/20');
                conc.innerHTML = `<strong>Downscale Required:</strong> Target size is too restrictive. Scaling to <strong>${best.w}x${best.h} @ ${best.f}fps</strong> to avoid pixelation.`;
            }
        }
    }

    const debounce = (f, w) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), w); }; };
    const debouncedCalc = debounce(calculateBPP, 500);
    ['calc-mb', 'calc-dur', 'calc-w', 'calc-h', 'calc-fps'].forEach(id => {
        const el = document.getElementById(id);
        if(el) el.addEventListener('input', debouncedCalc);
    });
    calculateBPP();

    // --- Hardware Matrix Logic ---
    const osData = {
        win: {
            title: "Windows Execution Priority", icon: "fa-windows", color: "text-blue-400",
            chain: [
                { name: "Nvidia NVENC", desc: "H.265/H.264 via dedicated hardware. Supports parallel 2-pass.", icon: "fa-microchip" },
                { name: "AMD AMF", desc: "H.265/H.264 via AMD's Media Framework.", icon: "fa-microchip" },
                { name: "Intel Quick Sync (QSV)", desc: "H.265/H.264 via Intel's integrated GPU.", icon: "fa-microchip" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "The script probes for encoders for the selected codec (H.265/HEVC or H.264). If dedicated GPU encoding fails or is unavailable, it defaults to CPU."
        },
        lin: {
            title: "Linux Execution Priority", icon: "fa-linux", color: "text-yellow-400",
            chain: [
                { name: "Nvidia NVENC", desc: "H.265/H.264 via dedicated hardware. Supports parallel 2-pass.", icon: "fa-microchip" },
                { name: "VA-API", desc: "Unified API for AMD and Intel hardware acceleration.", icon: "fa-layer-group" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "VA-API is a versatile API that covers both AMD and Intel hardware on Linux. NVENC is preferred if an Nvidia GPU is present."
        },
        mac: {
            title: "macOS Execution Priority", icon: "fa-apple", color: "text-slate-200",
            chain: [
                { name: "Apple VideoToolbox", desc: "Native API for Apple Silicon, AMD, and Intel hardware.", icon: "fa-apple" },
                { name: "CPU Fallback (libx265/libx264)", desc: "Software encoding. Most compatible, but slowest.", icon: "fa-server" }
            ],
            note: "VideoToolbox is the native macOS framework that abstracts hardware (Apple Silicon, Intel, AMD). Nvidia encoding is not supported on modern macOS."
        }
    };

    function renderChain(k) {
        const d = osData[k];
        const osTitle = document.getElementById('os-title');
        const osNote = document.getElementById('os-note');
        const priorityChain = document.getElementById('priority-chain');

        if(osTitle && osNote && priorityChain) {
            osTitle.innerHTML = `<i class="fa-brands ${d.icon} ${d.color} mr-3"></i>${d.title}`;
            osNote.textContent = d.note;
            priorityChain.innerHTML = '';
            d.chain.forEach((e, i) => {
                const el = document.createElement('div');
                el.className = `flex items-center p-4 bg-slate-800/50 rounded-lg border border-slate-700 hover:border-slate-500 transition-all ${i===0?'ring-1 ring-cyan-500/30':''}`;
                el.innerHTML = `<div class="w-8 h-8 rounded-full ${i===0?'bg-cyan-500 text-slate-900':'bg-slate-700 text-white'} flex items-center justify-center font-bold text-sm mr-4">${i+1}</div><div class="flex-grow"><div class="font-bold text-white font-mono text-sm">${e.name}</div><div class="text-xs text-slate-400">${e.desc}</div></div><i class="fa-solid ${e.icon} text-slate-600 text-xl ml-4"></i>`;
                priorityChain.appendChild(el);
            });
        }
    }

    document.querySelectorAll('.os-btn').forEach(b => b.addEventListener('click', e => {
        document.querySelectorAll('.os-btn').forEach(x => { x.classList.remove('os-btn-active'); x.classList.add('bg-slate-800', 'border-slate-600'); });
        const btn = e.target.closest('button'); 
        if(btn) {
            btn.classList.remove('bg-slate-800', 'border-slate-600'); 
            btn.classList.add('os-btn-active');
            renderChain(btn.id.replace('btn-', ''));
        }
    }));

    renderChain('win');
});
