import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.models import User  # إضافة استيراد موديل المستخدمين
from colorfield.fields import ColorField
from django_resized import ResizedImageField

# --- Categories ---

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True, null=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']

    def __str__(self):
        return self.name


# --- Products ---

class Product(models.Model):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="SKU")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2) 
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True) 
    stock = models.PositiveIntegerField(default=0, verbose_name="Total Stock", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    is_new_arrival = models.BooleanField(default=False, verbose_name="New Arrival?")
    new_arrival_updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"{self.name} ({self.sku or 'No SKU'})"

    def save(self, *args, **kwargs):
        if self.pk:
            old_instance = Product.objects.filter(pk=self.pk).first()
            if old_instance and self.is_new_arrival and not old_instance.is_new_arrival:
                self.new_arrival_updated_at = timezone.now()
        elif self.is_new_arrival:
            self.new_arrival_updated_at = timezone.now()

        if not self.sku:
            prefix = self.name[:3].upper() if self.name else "FUR"
            self.sku = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
        
        super().save(*args, **kwargs)

    def update_total_stock(self):
        total = ProductSize.objects.filter(variant__product=self).aggregate(total=Sum('stock'))['total'] or 0
        Product.objects.filter(pk=self.pk).update(stock=total)

    @property
    def get_effective_price(self):
        return self.discount_price if self.discount_price else self.price
        
    @property
    def discount_percentage(self):
        if self.price and self.discount_price and self.price > self.discount_price:
            discount = self.price - self.discount_price
            percentage = (discount / self.price) * 100
            return int(percentage)  # إرجاع الرقم كعدد صحيح (مثلاً 20 بدلاً من 20.0)
        return 0

    @property
    def is_new(self):
        if self.is_new_arrival and self.new_arrival_updated_at:
            return timezone.now() < self.new_arrival_updated_at + timedelta(days=7)
        return False

    @property
    def main_image(self):
        variant = self.variants.first()
        if variant and variant.variant_image:
            return variant.variant_image.url
        return None


# --- Product Specifications (الجدول الجديد للمواصفات) ---

class ProductSpecification(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='specifications')
    spec_name = models.CharField(max_length=255, verbose_name="اسم المواصفة (مثال: الألوان)")
    # تم تغيير الحقل إلى TextField ليسمح بإدخال قيم كثيرة جداً ومفصلة
    spec_value = models.TextField(verbose_name="القيم (يمكنك إدخال أكثر من قيمة مفصولة بفاصلة)")

    def __str__(self):
        return f"{self.spec_name}: {self.spec_value}"


# --- Variants & Inventory ---

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    color_name = models.CharField(max_length=50)
    color_code = ColorField(default='#000000') 
    variant_image = ResizedImageField(
        size=[800, 1000], quality=75, upload_to='variants/', 
        force_format='WEBP'
    )
    @property
    def total_stock(self):
        """حساب مجموع المخزن لكل المقاسات التابعة لهذا اللون"""
        return self.sizes.aggregate(total=models.Sum('stock'))['total'] or 0
    def __str__(self):
        return f"{self.product.name} - {self.color_name}"


class ProductImage(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='additional_images')
    image = ResizedImageField(
        size=[800, 1000], quality=75, upload_to='variants/extra/', 
        force_format='WEBP'
    )
    alt_text = models.CharField(max_length=200, blank=True, null=True)


class ProductSize(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='sizes')
    size_name = models.CharField(max_length=20)
    stock = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.variant} - {self.size_name}"


@receiver([post_save, post_delete], sender=ProductSize)
def update_product_stock_signal(sender, instance, **kwargs):
    instance.variant.product.update_total_stock()


# --- Orders ---

class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending ⏳'),
        ('Shipped', 'Shipped 🚚'),
        ('Delivered', 'Delivered ✅'),
        ('Canceled', 'Canceled ❌'),
    ]
    
    # إضافة حقل المستخدم لحل مشكلة TypeError في الـ Checkout
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    governorate = models.CharField(max_length=100)
    address = models.TextField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    is_completed = models.BooleanField(default=False)

    __original_status = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__original_status = self.status

    def save(self, *args, **kwargs):
        if self.pk and self.status != self.__original_status:
            self.send_status_notification()
            if self.status == 'Delivered':
                self.is_completed = True
        super().save(*args, **kwargs)

    def send_status_notification(self):
        subject = f"تحديث بخصوص طلبك رقم #{self.id} - متجر أبو يحيى"
        messages_map = {
            'Shipped': "طلبك في طريقه إليك الآن! 🚚",
            'Delivered': "تم توصيل طلبك بنجاح. نتمنى أن ينال إعجابك! ✅",
            'Canceled': "للأسف، تم إلغاء طلبك. تواصل معنا لمزيد من التفاصيل. ❌",
        }
        msg = messages_map.get(self.status, f"تم تحديث حالة طلبك إلى: {self.status}")
        try:
            send_mail(subject, msg, settings.EMAIL_HOST_USER, [self.email], fail_silently=True)
        except Exception: pass

    class Meta:
        ordering = ['-created_at']

    @property
    def get_items_total(self):
        """حساب مجموع أسعار جميع المنتجات في الطلب قبل الخصم اليدوي"""
        return sum(item.subtotal for item in self.items.all())

    @property
    def get_discount_amount(self):
        """حساب قيمة الخصم اليدوي المطبق"""
        total_items = self.get_items_total
        discount = total_items - self.total_price
        return discount if discount > 0 else 0


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    color = models.CharField(max_length=50)
    size = models.CharField(max_length=20, null=True, blank=True) 
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def subtotal(self):
        return self.quantity * self.price_at_purchase


# --- Contact ---

class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True) 
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)