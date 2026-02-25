from django.db import models
from colorfield.fields import ColorField
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
import uuid
from datetime import timedelta
from django_resized import ResizedImageField

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Categories"

class Product(models.Model):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="SKU (Stock Keeping Unit)")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products', null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2) 
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True) 
    stock = models.PositiveIntegerField(default=0, verbose_name="Total Stock Quantity", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    is_new_arrival = models.BooleanField(default=False, verbose_name="New Arrival?")
    new_arrival_updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"{self.name} ({self.sku if self.sku else 'No SKU'})"

    def save(self, *args, **kwargs):
        if self.pk:
            old_instance = Product.objects.filter(pk=self.pk).first()
            if old_instance and self.is_new_arrival and not old_instance.is_new_arrival:
                self.new_arrival_updated_at = timezone.now()
            elif not self.is_new_arrival:
                self.new_arrival_updated_at = None
        else:
            if self.is_new_arrival:
                self.new_arrival_updated_at = timezone.now()

        if not self.sku:
            prefix = self.name[:3].upper() if self.name else "PRD"
            unique_id = str(uuid.uuid4().hex[:6].upper())
            self.sku = f"{prefix}-{unique_id}"
        
        super().save(*args, **kwargs)

    def update_total_stock(self):
        """تحديث إجمالي المخزون للمنتج"""
        total = ProductSize.objects.filter(variant__product=self).aggregate(total=Sum('stock'))['total'] or 0
        Product.objects.filter(pk=self.pk).update(stock=total)

    @property
    def is_new(self):
        if self.is_new_arrival and self.new_arrival_updated_at:
            expiry_date = self.new_arrival_updated_at + timedelta(days=7)
            return timezone.now() < expiry_date
        return False

    @property
    def main_image(self):
        first_variant = self.variants.first()
        if first_variant and first_variant.variant_image:
            return first_variant.variant_image.url
        return None

    @property
    def is_out_of_stock(self):
        return self.stock <= 0

    @property
    def discount_percentage(self):
        if self.discount_price and self.price > 0:
            discount = ((self.price - self.discount_price) / self.price) * 100
            return int(discount)
        return 0

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    color_name = models.CharField(max_length=50)
    color_code = ColorField(default='#FF0000') 
    
    variant_image = ResizedImageField(
        size=[800, 1000], 
        quality=70, 
        upload_to='variants/', 
        force_format='WEBP',
        crop=['middle', 'center'],
        verbose_name="Main Image for this Color"
    )

    @property
    def total_stock(self):
        return self.sizes.aggregate(total=Sum('stock'))['total'] or 0

    def __str__(self):
        return f"{self.product.name} - {self.color_name}"

class ProductImage(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='additional_images')
    image = ResizedImageField(
        size=[800, 1000], 
        quality=70, 
        upload_to='variants/extra/', 
        force_format='WEBP',
        crop=['middle', 'center']
    )
    alt_text = models.CharField(max_length=200, blank=True, null=True, help_text="description")

    def __str__(self):
        return f"Image for {self.variant.product.name} - {self.variant.color_name}"

class ProductSize(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='sizes')
    size_name = models.CharField(max_length=20, verbose_name="Size (S, M, L, 42, etc.)")
    stock = models.PositiveIntegerField(default=5, verbose_name="Stock for this Size")

    def __str__(self):
        return f"{self.variant.product.name} - {self.variant.color_name} - {self.size_name}"

    def save(self, *args, **kwargs):
        # تم إزالة update_total_stock من هنا لتسريع عملية الرفع الجماعي
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        product = self.variant.product
        super().delete(*args, **kwargs)
        product.update_total_stock()

class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending ⏳'),
        ('Shipped', 'Shipped 🚚'),
        ('Delivered', 'Delivered ✅'),
        ('Canceled', 'Canceled ❌'),
    ]
    name = models.CharField(max_length=255, verbose_name="Customer Name")
    email = models.EmailField(verbose_name="Email Address")
    phone = models.CharField(max_length=20, verbose_name="Phone Number")
    governorate = models.CharField(max_length=100, verbose_name="Governorate")
    address = models.TextField(verbose_name="Full Address")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Amount")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending', verbose_name="Order Status")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Order Date")
    is_completed = models.BooleanField(default=False, verbose_name="Is Completed?")

    __original_status = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__original_status = self.status

    def __str__(self):
        return f"Order #{self.id} - {self.name}"

    def get_items_total(self):
        return sum(item.subtotal for item in self.items.all())

    def get_discount_amount(self):
        total_before = self.get_items_total()
        discount = total_before - self.total_price
        return discount if discount > 0 else 0

    def save(self, *args, **kwargs):
        if self.pk and self.status != self.__original_status:
            self.send_status_notification()
            if self.status == 'Delivered':
                self.is_completed = True
        super().save(*args, **kwargs)
        self.__original_status = self.status

    def send_status_notification(self):
        subject = f"Ice Club Store - Order #{self.id} Update"
        messages_map = {
            'Shipped': "Great news! Your order is now on its way to you. 🚚",
            'Delivered': "Your order has been delivered successfully! ✅",
            'Canceled': "We're sorry, but your order has been canceled. ❌",
        }
        status_msg = messages_map.get(self.status, f"Your order status has been updated to: {self.status}")
        email_body = f"Hi {self.name},\n\n{status_msg}\n\nThank you for choosing Ice Club Store!"
        try:
            send_mail(subject, email_body, settings.EMAIL_HOST_USER, [self.email], fail_silently=True)
        except Exception as e:
            print(f"Error sending email: {e}")

    class Meta:
        ordering = ['-created_at']

class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    color = models.CharField(max_length=50)
    size = models.CharField(max_length=20, null=True, blank=True) 
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.product.name if self.product else 'Deleted Product'} ({self.color})"

    @property
    def subtotal(self):
        return self.quantity * self.price_at_purchase

class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True) 
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.subject}"