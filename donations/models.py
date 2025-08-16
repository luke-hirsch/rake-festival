from django.db import models

class Donor(models.Model):
    name = models.CharField(max_length=100, default="Gönnjamin")
    email = models.EmailField(max_length=254, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or "Gönnjamin"

class Donation(models.Model):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    donor = models.ForeignKey(Donor, on_delete=models.SET_NULL, null=True, blank=True, related_name="donations")
    created_at = models.DateTimeField(auto_now_add=True)
    message_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    def __str__(self):
        return f"{self.amount} EUR"


class Beneficiary(models.Model):
    name = models.CharField(max_length=100)
    paypal_handle = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(max_length=254, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paypal_qrcode = models.ImageField(upload_to='qrcodes/', blank=True, null=True)

    def __str__(self):
        return self.name or "Anonymous"

class Goal(models.Model):
    title = models.CharField(max_length=100)
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.SET_NULL, null=True, blank=True, related_name="goals")
    description = models.TextField(blank=True)
    target_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
