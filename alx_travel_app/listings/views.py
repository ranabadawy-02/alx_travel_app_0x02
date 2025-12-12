import os
import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Payment
from django.core.mail import send_mail
from celery import shared_task

CHAPA_SECRET_KEY = os.getenv("CHAPA_SECRET_KEY")
CHAPA_API_BASE = "https://api.chapa.co/v1/transaction"

# -------------------------
# Celery task to send email
# -------------------------
@shared_task
def send_confirmation_email(email, booking_reference):
    send_mail(
        subject="Booking Payment Confirmation",
        message=f"Your payment for booking {booking_reference} has been successfully completed.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )

# -------------------------
# Initiate Payment
# -------------------------
class InitiatePaymentView(APIView):
    def post(self, request):
        data = request.data
        booking_ref = data.get("booking_reference")
        amount = data.get("amount")
        email = data.get("email")

        if not all([booking_ref, amount, email]):
            return Response({"error": "Missing required fields"}, status=status.HTTP_400_BAD_REQUEST)

        # Create Payment object with Pending status
        payment = Payment.objects.create(
            booking_reference=booking_ref,
            amount=amount,
            status="Pending",
            email=email
        )

        # Prepare Chapa payment request
        payload = {
            "amount": float(amount),
            "currency": "ETB",  # or USD
            "email": email,
            "tx_ref": booking_ref,
            "callback_url": f"http://localhost:8000/api/verify-payment/{booking_ref}/"
        }

        headers = {
            "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.post(f"{CHAPA_API_BASE}/initialize", json=payload, headers=headers)

        if response.status_code == 200:
            chapa_response = response.json()
            transaction_id = chapa_response.get("data", {}).get("id")
            payment.transaction_id = transaction_id
            payment.save()
            return Response({"payment_url": chapa_response.get("data", {}).get("checkout_url")})
        else:
            return Response({"error": "Failed to initiate payment"}, status=status.HTTP_400_BAD_REQUEST)

# -------------------------
# Verify Payment
# -------------------------
class VerifyPaymentView(APIView):
    def get(self, request, booking_reference):
        try:
            payment = Payment.objects.get(booking_reference=booking_reference)
        except Payment.DoesNotExist:
            return Response({"error": "Invalid booking reference"}, status=status.HTTP_404_NOT_FOUND)

        headers = {
            "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
        }

        response = requests.get(f"{CHAPA_API_BASE}/verify/{payment.transaction_id}", headers=headers)
        if response.status_code == 200:
            data = response.json().get("data", {})
            status_chapa = data.get("status")

            if status_chapa == "success":
                payment.status = "Completed"
                payment.save()
                send_confirmation_email.delay(payment.email, payment.booking_reference)
                return Response({"message": "Payment completed successfully"})
            else:
                payment.status = "Failed"
                payment.save()
                return Response({"message": "Payment failed"})
        else:
            return Response({"error": "Failed to verify payment"}, status=status.HTTP_400_BAD_REQUEST)
