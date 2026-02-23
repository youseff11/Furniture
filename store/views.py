import requests
import hashlib
import time
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.forms import inlineformset_factory
from .models import Product, Category, ContactMessage, ProductVariant, Order, OrderItem, ProductSize, ProductImage
from .forms import ProductForm
from django import forms
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from django.db import connection
from django.db.models import Case, When, Value, IntegerField
from django.contrib import messages
from decimal import Decimal
# FB_PIXEL_ID = '792214427202379'  
# FB_ACCESS_TOKEN = 'EAAXY0i6ZArdwBQUwZAq4Mx7ArysubuZAELX8l1XnZBVA1gqWwklibClR6Hrw5Ves0DhZCK5SjjtrqwZAfWeX6yZBCmzsqNlUlW4cwTk4NQFHcCqT2rKPxfPLKMbr6DxvK4Gg0XlNqJGhBVTWqvgQR92MvT9CamOHpNDiUQ2X7bDc7s3LxXQZB6I9vSKs9R8u0ZCWv8gZDZD'
# FB_API_VERSION = 'v18.0'

# def send_fb_capi_event(request, event_name, event_id=None, user_data=None, custom_data=None):
#     url = f"https://graph.facebook.com/{FB_API_VERSION}/{FB_PIXEL_ID}/events"
    
#     if not event_id:
#         event_id = f"server_{int(time.time())}_{hashlib.md5(request.META.get('HTTP_USER_AGENT', '').encode()).hexdigest()[:6]}"

#     payload_user_data = {
#         "client_ip_address": request.META.get('REMOTE_ADDR'),
#         "client_user_agent": request.META.get('HTTP_USER_AGENT'),
#     }
    
#     if user_data:
#         for key, value in user_data.items():
#             if value:
#                 payload_user_data[key] = hashlib.sha256(str(value).lower().strip().encode()).hexdigest()

#     data = {
#         "data": [
#             {
#                 "event_name": event_name,
#                 "event_id": event_id,  
#                 "event_time": int(time.time()),
#                 "action_source": "website",
#                 "event_source_url": request.build_absolute_uri(),
#                 "user_data": payload_user_data,
#                 "custom_data": custom_data or {}
#             }
#         ],
#         "access_token": FB_ACCESS_TOKEN
#     }

#     try:
#         requests.post(url, json=data)
#     except Exception as e:
#         print(f"Facebook CAPI Error: {e}")

VariantFormSet = inlineformset_factory(
    Product, 
    ProductVariant, 
    fields=['color_name', 'color_code', 'variant_image'],
    extra=3, 
    can_delete=True,
    widgets={
        'color_code': forms.TextInput(attrs={
            'type': 'color', 
            'class': 'form-control'
        }),
        'color_name': forms.TextInput(attrs={
            'placeholder': 'e.g. Black', 
            'class': 'form-control'
        }),
    }
)

def home(request):
    return render(request, 'home.html')

def shop_view(request, category_slug=None):
    categories = Category.objects.all()
    products = Product.objects.annotate(
        is_available_group=Case(
            When(stock__gt=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        manual_new_priority=Case(
            When(is_new_arrival=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by('is_available_group', '-manual_new_priority', '-created_at')

    selected_category = None
    if category_slug:
        selected_category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=selected_category)

    context = {
        'products': products,
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'shop.html', context)

def product_detail(request, id):
    product = get_object_or_404(Product, id=id)    
    price = float(product.discount_price if product.discount_price else product.price)
    send_fb_capi_event(
        request, 
        "ViewContent", 
        custom_data={
            "content_ids": [str(product.id)],
            "content_name": product.name,
            "content_type": "product",
            "value": price,
            "currency": "EGP"
        }
    )
    
    return render(request, 'product_detail.html', {'product': product})

def contact_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        subject = request.POST.get('subject') or "No Subject"
        message = request.POST.get('message')

        ContactMessage.objects.create(
            name=name, 
            email=email, 
            phone=phone,
            subject=subject, 
            message=message
        )

        full_message = f"New message from {name}\nEmail: {email}\nPhone: {phone}\n\nMessage:\n{message}"
        
        try:
            send_mail(
                subject=f"Ice Club Store: {subject}",
                message=full_message,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[settings.EMAIL_HOST_USER],
                fail_silently=False,
            )
            messages.success(request, 'Sent! We received your message.')
        except Exception as e:
            messages.warning(request, 'Message saved, but email notification failed.')

        return redirect('contact')

    return render(request, 'contact.html')

def add_to_cart(request, product_id):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    selected_color = request.GET.get('color', 'Default') 
    selected_size = request.GET.get('size', 'N/A')    
    e_id = request.GET.get('eid')    
    item_key = f"{product_id}_{selected_color}_{selected_size}"
    
    try:
        # تعديل هنا: استخدام size_name بدلاً من size
        stock_item = ProductSize.objects.get(
            variant__product_id=product_id,
            variant__color_name=selected_color,
            size_name=selected_size
        )
        
        current_qty = cart.get(item_key, {}).get('quantity', 0)
        
        if current_qty < stock_item.stock:
            if item_key in cart:
                cart[item_key]['quantity'] += 1
            else:
                cart[item_key] = {
                    'product_id': product_id,
                    'quantity': 1,
                    'color': selected_color,
                    'size': selected_size
                }
            
            product = get_object_or_404(Product, id=product_id)
            price = float(product.discount_price if product.discount_price else product.price)    
            
            send_fb_capi_event(
                request, 
                "AddToCart", 
                event_id=e_id,
                custom_data={
                    "content_ids": [str(product_id)],
                    "content_name": product.name,
                    "content_type": "product",
                    "value": price,
                    "currency": "EGP"
                }
            )
            
            request.session[user_cart_key] = cart
            request.session.modified = True
            messages.success(request, f'Added to cart ({selected_color} - {selected_size})!')
            
        else:
            messages.warning(request, f"Sorry, only {stock_item.stock} units available.")
            
    except ProductSize.DoesNotExist:
        messages.error(request, "This combination is not available.")

    return redirect(request.META.get('HTTP_REFERER', 'shop'))

def cart_view(request):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    cart_items = []
    total_price = 0
    
    if not isinstance(cart, dict):
        cart = {}
        request.session[user_cart_key] = cart

    for item_key, item_data in cart.items():
        if not isinstance(item_data, dict):
            continue
            
        try:
            product = Product.objects.get(id=item_data.get('product_id'))
            quantity = item_data.get('quantity', 1)
            actual_price = product.discount_price if product.discount_price else product.price
            subtotal = actual_price * quantity
            total_price += subtotal
            
            variant = ProductVariant.objects.filter(product=product, color_name=item_data.get('color')).first()
            display_image = variant.variant_image.url if variant else product.main_image.url
            
            cart_items.append({
                'item_key': item_key,
                'product': product,
                'quantity': quantity,
                'color': item_data.get('color'),
                'size': item_data.get('size', 'N/A'),
                'display_image': display_image,
                'subtotal': subtotal,
                'actual_price': actual_price
            })
        except (Product.DoesNotExist, AttributeError):
            continue
        
    return render(request, 'cart.html', {'cart_items': cart_items, 'total_price': total_price})

def update_cart(request, item_key, action):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    
    if item_key in cart:
        if action == 'increase':
            item_data = cart[item_key]
            product_id = item_data['product_id']
            color = item_data['color']
            size_val = item_data['size'] # القيمة المخزنة في السيشن
            
            try:
                # تعديل هنا: استخدام size_name بدلاً من size
                stock_item = ProductSize.objects.get(
                    variant__product_id=product_id,
                    variant__color_name=color,
                    size_name=size_val
                )
                
                if cart[item_key]['quantity'] < stock_item.stock:
                    cart[item_key]['quantity'] += 1
                else:
                    messages.warning(request, f"Only {stock_item.stock} units left.")
            except ProductSize.DoesNotExist:
                messages.error(request, "Stock error occurred.")
                
        elif action == 'decrease':
            cart[item_key]['quantity'] -= 1
            if cart[item_key]['quantity'] <= 0: 
                del cart[item_key]
                
        request.session[user_cart_key] = cart
        request.session.modified = True
        
    return redirect('cart_view')
    
def remove_from_cart(request, item_key):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    if item_key in cart:
        del cart[item_key]
        request.session[user_cart_key] = cart
        request.session.modified = True
    return redirect('cart_view')

def checkout(request):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    
    if not cart:
        messages.warning(request, "Your cart is empty!")
        return redirect('shop')

    total_price = 0
    checkout_items = []
    
    for item_key, item_data in cart.items():
        product = get_object_or_404(Product, id=item_data['product_id'])
        color_name = item_data.get('color')
        size_name = item_data.get('size')
        quantity_requested = item_data['quantity']
        
        variant_size = ProductSize.objects.filter(
            variant__product=product, 
            variant__color_name=color_name, 
            size_name=size_name
        ).first()
        
        if variant_size:
            if variant_size.stock < quantity_requested:
                messages.error(request, f"Sorry, only {variant_size.stock} left for {product.name} ({color_name} - {size_name}).")
                return redirect('cart_view')
        else:
            if product.stock < quantity_requested:
                messages.error(request, f"Sorry, {product.name} is out of stock.")
                return redirect('cart_view')

        price = product.discount_price if product.discount_price else product.price
        subtotal = price * quantity_requested
        total_price += subtotal

        variant = ProductVariant.objects.filter(product=product, color_name=color_name).first()
        img_path = variant.variant_image.url if variant and variant.variant_image else product.main_image.url
        
        domain = request.get_host()
        protocol = 'https' if request.is_secure() else 'http'
        image_url = f"{protocol}://{domain}{img_path}"

        checkout_items.append({
            'product': product, 
            'subtotal': subtotal, 
            'data': item_data, 
            'variant_size': variant_size,
            'unit_price': price,
            'image_url': image_url
        })

    if request.method == 'GET' and total_price > 0:
        send_fb_capi_event(
            request, 
            "InitiateCheckout", 
            custom_data={"value": float(total_price), "currency": "EGP"}
        )

    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        governorate = request.POST.get('governorate')
        address = request.POST.get('address')

        order = Order.objects.create(
            name=name, email=email, phone=phone,
            governorate=governorate, address=address,
            total_price=total_price
        )
        
        if request.user.is_authenticated:
            order.user = request.user
            order.save()

        send_fb_capi_event(
            request, 
            "Purchase", 
            event_id=str(order.id), 
            user_data={"em": email, "ph": phone},
            custom_data={
                "value": float(total_price), 
                "currency": "EGP", 
                "order_id": str(order.id),
                "content_type": "product",
            }
        )

        email_items_html = ""
        for item in checkout_items:
            product = item['product']
            variant_size = item['variant_size']
            qty = item['data']['quantity']
            color = item['data']['color']
            size = item['data']['size']
            price_each = item['unit_price']
            img = item['image_url']
            sku = product.sku if hasattr(product, 'sku') and product.sku else "N/A"

            OrderItem.objects.create(
                order=order, product=product, color=color, size=size,
                quantity=qty, price_at_purchase=price_each
            )

            email_items_html += f"""
                <tr>
                    <td style="padding: 12px; border-bottom: 1px solid #eee; vertical-align: middle;">
                        <img src="{img}" width="60" height="60" style="border-radius:8px; margin-right:12px; vertical-align:middle; border:1px solid #ddd; object-fit: cover;">
                        <div style="display: inline-block; vertical-align: middle;">
                            <strong style="font-size: 15px; color: #333;">{product.name}</strong><br>
                            <span style="font-size: 12px; color: #888;">SKU: {sku}</span><br>
                            <span style="font-size: 12px; color: #555;">Color: {color} | Size: {size}</span>
                        </div>
                    </td>
                    <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:center;">{qty}</td>
                    <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:right; font-weight: bold;">{int(price_each * qty)} EGP</td>
                </tr>
            """

            if variant_size:
                variant_size.stock -= qty
                variant_size.save()
            else:
                product.stock -= qty
                product.save()

        html_message = f"""
        <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: auto; border: 1px solid #f0f0f0; border-radius: 15px; overflow: hidden; background-color: #ffffff;">
            <div style="background-color: #000000; color: #ffffff; padding: 30px; text-align: center;">
                <h1 style="margin: 0; font-size: 28px; letter-spacing: 2px;">ICE CLUB</h1>
                <p style="margin: 5px 0 0; opacity: 0.7;">Order Confirmation #{order.id}</p>
            </div>
            <div style="padding: 30px;">
                <h2 style="color: #333; margin-top: 0;">Hi {name},</h2>
                <p style="color: #666; line-height: 1.6;">Thank you for your purchase! We've received your order and we're getting it ready for shipment.</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 25px;">
                    <thead>
                        <tr style="background-color: #fafafa; border-bottom: 2px solid #333;">
                            <th style="text-align: left; padding: 12px; color: #333;">Product Details</th>
                            <th style="text-align: center; padding: 12px; color: #333;">Qty</th>
                            <th style="text-align: right; padding: 12px; color: #333;">Subtotal</th>
                        </tr>
                    </thead>
                    <tbody>{email_items_html}</tbody>
                    <tfoot>
                        <tr>
                            <td colspan="2" style="padding: 20px 10px; text-align: right; font-size: 16px; color: #777;">Grand Total:</td>
                            <td style="padding: 20px 0; text-align: right; font-size: 22px; font-weight: bold; color: #d63031;">{int(total_price)} EGP</td>
                        </tr>
                    </tfoot>
                </table>
                <div style="margin-top: 30px; padding: 20px; background-color: #f9f9f9; border-radius: 10px;">
                    <h4 style="margin: 0 0 10px 0; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 5px;">Shipping Information</h4>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>Address:</strong> {address}</p>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>City:</strong> {governorate}</p>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>Phone:</strong> {phone}</p>
                </div>
            </div>
            <div style="background-color: #f4f4f4; padding: 15px; text-align: center; font-size: 11px; color: #999;">
                This is an automated message. Please do not reply directly to this email.<br>
                © 2026 Ice Club Store. All rights reserved.
            </div>
        </div>
        """

        subject = f"Ice Club - Order Confirmation #{order.id}"
        plain_message = strip_tags(html_message)
        
        try:
            send_mail(
                subject, 
                plain_message, 
                settings.EMAIL_HOST_USER, 
                [email, settings.EMAIL_HOST_USER], 
                html_message=html_message,
                fail_silently=True
            )
        except:
            pass

        request.session[user_cart_key] = {}
        request.session.modified = True
        return render(request, 'order_success.html', {'order': order})

    return render(request, 'checkout.html', {'total_price': total_price})

def is_admin(user):
    return user.is_authenticated and user.is_staff

@user_passes_test(is_admin, login_url='login')
def dashboard_view(request):
    orders = Order.objects.all().order_by('-created_at')
    products = Product.objects.all().order_by('-created_at')
    messages_list = ContactMessage.objects.all().order_by('-created_at')
    
    # حساب الإجمالي فقط للطلبات المكتملة
    total_revenue = sum(order.total_price for order in orders if order.status == 'Delivered')
    
    context = {
        'orders': orders,
        'products': products,
        'messages': messages_list,
        'orders_count': orders.count(),
        'pending_orders': orders.filter(status='Pending').count(),
        'shipped_orders': orders.filter(status='Shipped').count(),
        'delivered_orders': orders.filter(status='Delivered').count(),
        'products_count': products.count(),
        'total_revenue': total_revenue,
    }
    return render(request, 'dashboard.html', context)
@user_passes_test(is_admin, login_url='login')
def add_product(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        formset = VariantFormSet(request.POST, request.FILES)
        
        if form.is_valid() and formset.is_valid():
            product = form.save()
            
            for i, v_form in enumerate(formset.forms):
                if v_form.cleaned_data and not v_form.cleaned_data.get('DELETE', False):
                    variant = v_form.save(commit=False)
                    variant.product = product
                    variant.save()
                    v_form.save_m2m()
                    
                    size_names = request.POST.getlist(f'size_name_{i}[]')
                    size_quantities = request.POST.getlist(f'size_qty_{i}[]')
                    
                    for name, qty in zip(size_names, size_quantities):
                        if name.strip():
                            ProductSize.objects.create(
                                variant=variant,
                                size_name=name.strip(),
                                stock=int(qty) if qty else 0
                            )

                    extra_images = request.FILES.getlist(f'images_custom_{i}')
                    for img in extra_images:
                        ProductImage.objects.create(variant=variant, image=img)

            messages.success(request, 'Product, Variants, Sizes, and Gallery added successfully! ✅')
            return redirect('dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ProductForm()
        formset = VariantFormSet()
    
    return render(request, 'manage_product.html', {
        'form': form, 
        'formset': formset, 
        'title': 'Add New Product'
    })

@user_passes_test(is_admin, login_url='login')
def edit_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        formset = VariantFormSet(request.POST, request.FILES, instance=product)
        
        if form.is_valid() and formset.is_valid():
            form.save()
            
            for i, v_form in enumerate(formset.forms):
                if v_form.cleaned_data and not v_form.cleaned_data.get('DELETE', False):
                    variant = v_form.save(commit=False)
                    variant.product = product
                    variant.save()
                    v_form.save_m2m()
                    
                    size_names = request.POST.getlist(f'size_name_{i}[]')
                    size_quantities = request.POST.getlist(f'size_qty_{i}[]')
                    
                    if size_names:
                        variant.product_sizes.all().delete()
                        for name, qty in zip(size_names, size_quantities):
                            if name.strip():
                                ProductSize.objects.create(
                                    variant=variant, 
                                    size_name=name.strip(),
                                    stock=int(qty) if qty else 0
                                )

                    extra_images = request.FILES.getlist(f'images_custom_{i}')
                    for img in extra_images:
                        ProductImage.objects.create(variant=variant, image=img)
                
                elif v_form.cleaned_data.get('DELETE', False) and v_form.instance.pk:
                    v_form.instance.delete()

            messages.success(request, 'Product updated successfully! ✨')
            return redirect('dashboard')
    else:
        form = ProductForm(instance=product)
        formset = VariantFormSet(instance=product)
    
    return render(request, 'manage_product.html', {
        'form': form, 
        'formset': formset, 
        'title': f'Edit: {product.name}'
    })

@user_passes_test(is_admin, login_url='login')
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.delete()
    messages.error(request, 'Product has been deleted! 🗑️')
    return redirect('dashboard')

@user_passes_test(is_admin, login_url='login')
def update_order_status(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        new_status = request.POST.get('status')        
        valid_choices = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status in valid_choices:
            order.status = new_status
            order.save() 
            messages.success(request, f'Order #{order.id} updated to {new_status}')
        else:
            messages.error(request, f'Error: {new_status} is not a valid status.')
            
    return redirect('dashboard')
@user_passes_test(is_admin, login_url='login')
def update_item_quantity(request, item_id):
    if request.method == 'POST':
        item = get_object_or_404(OrderItem, id=item_id)
        order = item.order
        # استقبال الأكشن من الفورم (سواء كان تحديث أو حذف)
        action = request.POST.get('action', 'update') 
        product_name = item.product.name if item.product else "منتج غير مسمى"

        if action == 'delete':
            # 1. منطق الحذف
            item.delete()
            
            # التحقق: إذا كان هذا آخر منتج في الطلب، نقوم بإلغاء الطلب أو حذفه
            if not order.items.exists():
                order.status = 'Canceled' # أو order.delete() حسب رغبتك
                order.total_price = 0
                order.save()
                messages.warning(request, f'تم حذف المنتج الأخير، لذا تم تحويل حالة الطلب #{order.id} إلى "ملغي".')
                subject = f"Order #{order.id} Canceled - Ice Club"
                email_content = f"Hi {order.name},\n\nYour order has been canceled because all items were removed."
            else:
                # تحديث إجمالي السعر بعد حذف عنصر واحد وبقاء آخرين
                new_total = sum(i.quantity * i.price_at_purchase for i in order.items.all())
                order.total_price = new_total
                order.save()
                messages.success(request, f'تم إزالة {product_name} من الطلب بنجاح.')
                subject = f"Order Update: Item Removed from Order #{order.id}"
                email_content = f"Hi {order.name},\n\nThe item ({product_name}) has been removed from your order as requested.\nNew Total: {order.total_price} EGP"
        
        else:
            # 2. منطق التحديث (Update)
            new_qty = int(request.POST.get('quantity', 1))
            if new_qty > 0:
                old_qty = item.quantity
                item.quantity = new_qty
                item.save()
                
                # تحديث السعر الإجمالي للطلب
                new_total = sum(Decimal(str(i.quantity * i.price_at_purchase)) for i in order.items.all())
                order.total_price = new_total
                order.save()
                
                messages.success(request, f'تم تحديث كمية {product_name} بنجاح.')
                subject = f"Order Update: Quantity Changed for #{order.id}"
                email_content = f"Hi {order.name},\n\nThe quantity of ({product_name}) was updated from {old_qty} to {new_qty}.\nNew Total: {order.total_price} EGP"
            else:
                messages.error(request, 'الكمية يجب أن تكون 1 على الأقل. يمكنك حذف المنتج بدلاً من ذلك.')
                return redirect('dashboard')

        # 3. إرسال الإيميل (مشترك للحالتين)
        try:
            send_mail(subject, email_content, settings.EMAIL_HOST_USER, [order.email], fail_silently=True)
        except Exception as e:
            print(f"Email failed: {e}")
    return redirect('dashboard')
    
@user_passes_test(is_admin, login_url='login')
def apply_order_discount(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        discount_input = request.POST.get('discount_amount', '0')
        
        try:
            # Convert to Decimal to match total_price type
            discount_amount = Decimal(discount_input)
        except (ValueError, TypeError):
            discount_amount = Decimal('0')

        if discount_amount >= 0:
            # Recalculate original total before applying the discount
            original_total = sum(Decimal(str(i.quantity * i.price_at_purchase)) for i in order.items.all())
            
            if discount_amount <= original_total:
                # Update the total price in the database
                order.total_price = original_total - discount_amount
                order.save()
                
                # Prepare the email content with "Before" and "After" pricing
                subject = f"Update on your Order #{order.id} - Ice Club Store"
                
                message = f"""
                Hello {order.name},
                
                We have great news! A special discount has been applied to your order.
                
                Price Breakdown:
                ---------------------------
                Subtotal: {original_total} EGP
                Discount Applied: - {discount_amount} EGP
                ---------------------------
                New Grand Total: {order.total_price} EGP
                
                We hope you enjoy your purchase!
                Thank you for shopping with Ice Club Store.
                """
                
                try:
                    send_mail(
                        subject, 
                        message, 
                        settings.EMAIL_HOST_USER, 
                        [order.email], 
                        fail_silently=True
                    )
                except Exception as e:
                    print(f"Email failed: {e}")

                messages.success(request, f'Discount of {discount_amount} EGP applied. Client notified via email.')
            else:
                messages.error(request, 'Discount cannot exceed the order total.')
        else:
            messages.error(request, 'Invalid discount amount.')
            
    return redirect('dashboard')
def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            messages.success(request, f'Welcome back, {username}!')
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password')
    return render(request, 'login.html')

def signup_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        User.objects.create_user(username=username, email=email, password=password)
        messages.success(request, 'Account created! Please login.')
        return redirect('login')
    return render(request, 'signup.html')

def logout_view(request):
    logout(request)
    return redirect('home')

def about_view(request):
    return render(request, 'about.html')

def offers_view(request):
    products = Product.objects.filter(
        discount_price__gt=0
    ).annotate(
        is_available_group=Case(
            When(stock__gt=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        manual_new_priority=Case(
            When(is_new_arrival=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by('is_available_group', '-manual_new_priority', '-created_at')

    context = {
        'products': products,
        'title': 'Exclusive Offers'
    }
    return render(request, 'offers.html', context)

def policies(request):
    return render(request, 'policies.html')

def reset_orders(request):
    if request.method == "POST":
        try:
            Order.objects.all().delete()            
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='store_order'")
            
            messages.success(request, "All Orders Are Deleted")
        except Exception as e:
            messages.error(request, f"Error resetting orders: {e}")
            
    return redirect('dashboard')