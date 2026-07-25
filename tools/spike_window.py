"""Smallest thing that proves the stack bundles: a WebView2 window that has
already imported the heavy media deps."""
import sys, time
t0 = time.perf_counter()
import numpy, PIL, moviepy          # noqa: F401 — presence is the point
import webview
elapsed = time.perf_counter() - t0
html = f"<h1>AVG spike</h1><p>heavy imports: {elapsed:.2f}s<br>python {sys.version}</p>"
webview.create_window("AVG spike", html=html, width=520, height=320)
webview.start()
