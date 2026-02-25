from django.contrib import admin
from django.utils.html import format_html
import nested_admin
from .models import Product, Category, ContactMessage, ProductVariant, ProductSize, Order, OrderItem, ProductImage

# --- 1. ProductImageInline ---
class ProductImageInline(nested_admin.NestedTabularInline):
    model = ProductImage
    extra = 1
    fields = ['image', 'image_preview']
    readonly_fields = ['image_preview']

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width: 70px; height: 70px; border-radius: 5px; object-fit: cover;" />', obj.image.url)
        return "No Image"
    image_preview.short_description = 'Preview'

# --- 2. ProductSizeInline ---
class ProductSizeInline(nested_admin.NestedTabularInline):
    model = ProductSize
    extra = 1
    fields = ['size_name', 'stock']

# --- 3. ProductVariantInline ---
class ProductVariantInline(nested_admin.NestedStackedInline):
    model = ProductVariant
    extra = 1
    fields = ['color_name', 'color_code', 'variant_image', 'image_preview']
    readonly_fields = ['image_preview']
    inlines = [ProductSizeInline, ProductImageInline]

    def image_preview(self, obj):
        if obj.variant_image:
            return format_html('<img src="{}" style="width: 100px; height: 100px; border-radius: 8px; border: 1px solid #ddd; object-fit: cover;" />', obj.variant_image.url)
        return "No Image"
    image_preview.short_description = 'Main Image Preview'

# --- 4. ProductAdmin ---
@admin.register(Product)
class ProductAdmin(nested_admin.NestedModelAdmin):
    inlines = [ProductVariantInline]
    
    list_display = ['display_image', 'sku', 'name', 'category', 'is_new_arrival', 'display_new_status', 'colored_stock', 'display_price', 'created_at']
    list_display_links = ['display_image', 'name']
    list_editable = ['sku', 'category', 'is_new_arrival']
    list_filter = ['category', 'is_new_arrival', 'created_at']
    search_fields = ['sku', 'name', 'description']

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'sku', 'category', 'description', 'is_new_arrival'),
            'classes': ('wide',),
        }),
        ('Pricing & Inventory', {
            'fields': (('price', 'discount_price'), 'stock'),
        }),
    )
    readonly_fields = ['stock']

    class Media:
        css = {
            'all': ('css/admin_custom.css',)
        }

    def display_image(self, obj):
        variant = obj.variants.first() 
        if variant and variant.variant_image:
            return format_html('<img src="{}" style="width: 50px; height: 50px; border-radius: 5px; object-fit: cover; border: 1px solid #eee;" />', variant.variant_image.url)
        return format_html('<span style="color: #999; font-size: 10px;">No Image</span>')
    display_image.short_description = 'Product Image'

    def display_new_status(self, obj):
        if hasattr(obj, 'is_new') and obj.is_new: 
            return format_html('<span style="color: #fff; background: #4fc3f7; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: bold;">LIVE NOW</span>')
        if obj.is_new_arrival:
            return format_html('<span style="color: #d32f2f; font-size: 11px; font-weight: bold;">MANUAL NEW</span>')
        return format_html('<span style="color: #999; font-size: 11px;">Standard</span>')
    display_new_status.short_description = 'Status Badge'

    def display_price(self, obj):
        original_price = int(obj.price) if obj.price else 0
        discount_price = int(obj.discount_price) if obj.discount_price else 0

        if discount_price > 0:
            return format_html(
                '<span style="text-decoration: line-through; color: #888; margin-right: 5px;">{}</span>'
                '<b style="color: #e91e63;">{}</b> <small>EGP</small>',
                original_price, discount_price
            )
        return format_html('<b>{}</b> <small>EGP</small>', original_price)
    display_price.short_description = 'Price (Before/After)'

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.update_total_stock()

    def colored_stock(self, obj):
        color = 'green' if obj.stock > 10 else 'orange' if obj.stock > 0 else 'red'
        return format_html('<b style="color: {};">{}</b>', color, obj.stock)
    colored_stock.short_description = 'Stock'

# --- 5. CategoryAdmin ---
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    
    class Media:
        css = {
            'all': ('css/admin_custom.css',)
        }

# --- 6. ContactMessageAdmin ---
@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ['name', 'subject', 'email', 'created_at']
    readonly_fields = ['name', 'email', 'phone', 'subject', 'message', 'created_at']

# --- 7. OrderItemInline & OrderAdmin ---
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    # تم إضافة 'display_item_image' هنا لعرض صورة المنتج
    readonly_fields = ['display_item_image', 'product', 'color', 'size', 'quantity', 'display_item_price']
    fields = ['display_item_image', 'product', 'color', 'size', 'quantity', 'display_item_price']
    can_delete = False

    def display_item_image(self, obj):
        """جلب صورة المنتج بناءً على اللون المختار في الطلب"""
        if obj.product:
            # البحث عن الـ Variant الذي يطابق اللون المكتوب في الطلب
            variant = obj.product.variants.filter(color_name=obj.color).first()
            if variant and variant.variant_image:
                return format_html('<img src="{}" style="width: 60px; height: 60px; border-radius: 5px; object-fit: cover; border: 1px solid #ddd;" />', variant.variant_image.url)
            
            # إذا لم يجد اللون، يعرض أول صورة متاحة للمنتج
            first_variant = obj.product.variants.first()
            if first_variant and first_variant.variant_image:
                return format_html('<img src="{}" style="width: 60px; height: 60px; border-radius: 5px; object-fit: cover; opacity: 0.6;" />', first_variant.variant_image.url)
                
        return format_html('<div style="width: 60px; height: 60px; background: #f0f0f0; display: flex; align-items: center; justify-content: center; border-radius: 5px; color: #ccc; font-size: 10px;">No Image</div>')
    
    display_item_image.short_description = 'Item Preview'

    def display_item_price(self, obj):
        return int(obj.price_at_purchase) if obj.price_at_purchase else 0
    display_item_price.short_description = 'Price'

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'phone', 'governorate', 'display_total', 'status', 'is_completed', 'created_at']
    list_filter = ['status', 'is_completed', 'governorate', 'created_at']
    search_fields = ['name', 'phone', 'email', 'id']
    list_editable = ['status', 'is_completed'] 
    inlines = [OrderItemInline]
    
    fieldsets = (
        ('Customer Info', {'fields': (('name', 'email'), 'phone', 'governorate', 'address')}),
        ('Status & Total', {'fields': (('status', 'is_completed'), 'total_price')}),
    )
    readonly_fields = ['total_price']

    class Media:
        css = {
            'all': ('css/admin_custom.css',)
        }

    def display_total(self, obj):
        return format_html('<b>{}</b> EGP', int(obj.total_price)) if obj.total_price else 0
    display_total.short_description = 'Total'