from django.contrib import admin
from .models import Donation, Donor, Goal

@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ("amount", "donor", "created_at")
    list_select_related = ("donor",)
    search_fields = ("donor__name", "donor__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

@admin.register(Donor)
class DonorAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "created_at")
    search_fields = ("name", "email")
    ordering = ("name",)

@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("title", "target_amount", "created_at")
    ordering = ("-created_at",)
