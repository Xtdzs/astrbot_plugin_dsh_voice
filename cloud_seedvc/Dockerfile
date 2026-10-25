FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 build-essential && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# Keep upstream source/license intact; the bot plugin never installs these packages.
RUN git clone https://github.com/Plachtaa/seed-vc.git /home/user/seed-vc && cd /home/user/seed-vc && git checkout 51383efd921027683c89e5348211d93ff12ac2a8
ENV SEEDVC_ROOT=/home/user/seed-vc
RUN pip install --no-cache-dir torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu121
RUN python -c "from pathlib import Path; p=Path('/home/user/seed-vc/requirements.txt'); lines=[s for s in p.read_text().splitlines() if s and not s.startswith(('torch', 'gradio', 'FreeSimpleGUI', 'sounddevice'))]; Path('/tmp/seed-requirements.txt').write_text('\n'.join(lines))" && pip install --no-cache-dir -r /tmp/seed-requirements.txt
COPY --chown=user requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user app.py ./app.py
EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log"]
