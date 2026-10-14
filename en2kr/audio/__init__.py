"""Audio capture sources.

Every source delivers 16 kHz mono float32 numpy chunks to ``on_audio`` from its
own thread and calls ``on_end(reason)`` once when it stops.
"""
SAMPLE_RATE = 16000
