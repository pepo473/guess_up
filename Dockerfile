FROM python:3.9
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# بورت 7860 هو اللي Hugging Face بيفتحه للمستخدمين
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", "0.0.0.0:7860", "app:app"]