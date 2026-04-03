FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir git+https://github.com/VectifyAI/PageIndex.git

COPY handler.py .

CMD ["handler.lambda_handler"]
