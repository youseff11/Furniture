import requests
import hashlib
import time
import json
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.forms import inlineformset_factory
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from django.db import connection, transaction
from django.db.models import Case, When, Value, IntegerField, Sum, F

from .models import (
    Product, Category, ContactMessage, ProductVariant, 
    Order, OrderItem, ProductSize, ProductImage
)
from .forms import ProductForm

# --- الدوال المساعدة (Helper Functions) ---

def get_user_cart_key(request):
    """إرجاع مفتاح الجلسة المناسب للسلة بناءً على حالة المستخدم"""
    if request.user.is_authenticated:
        return f"cart_{request.user.id}"
    return "cart_guest"

def is_admin(user):
    """التحقق مما إذا كان المستخدم مسؤولاً"""
    return user.is_authenticated and user.is_staff

# --- طرق العرض العامة (Public Views) ---

def home(request):
    return render(request, 'home.html')

from django.core.paginator import Paginator # تأكد من استيراد الموزع

def shop_view(request, category_slug=None):
    categories = Category.objects.all()
    
    # تحسين الأداء باستخدام prefetch_related لجلب بيانات الألوان والمقاسات في استعلام واحد
    products_list = Product.objects.prefetch_related('variants__sizes', 'variants__additional_images').annotate(
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
        products_list = products_list.filter(category=selected_category)

    # --- بداية كود التقسيم (Pagination) ---
    # تقسيم القائمة لعرض 20 منتج فقط في كل صفحة
    paginator = Paginator(products_list, 20) 
    page_number = request.GET.get('page')
    products = paginator.get_page(page_number)
    # --- نهاية كود التقسيم ---

    context = {
        'products': products, # سيحتوي الآن على 20 منتجاً فقط للصفحة الحالية
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'shop.html', context)

def product_detail(request, id):
    # استخدام prefetch_related لجلب المتغيرات والصور بكفاءة
    product = get_object_or_404(
        Product.objects.prefetch_related('variants__sizes', 'variants__additional_images'), 
        id=id
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
            name=name, email=email, phone=phone,
            subject=subject, message=message
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
            messages.success(request, 'شكراً لتواصلك معنا! تم استلام رسالتك.')
        except Exception:
            messages.warning(request, 'تم حفظ الرسالة بنجاح، ولكن تعذر إرسال إشعار البريد الإلكتروني حالياً.')

        return redirect('contact')

    return render(request, 'contact.html')

# --- منطق سلة المشتريات (Cart Logic) ---

def add_to_cart(request, product_id):
    user_cart_key = get_user_cart_key(request)
    cart = request.session.get(user_cart_key, {})
    
    selected_color = request.GET.get('color', 'Default') 
    selected_size = request.GET.get('size', 'N/A')    
    item_key = f"{product_id}_{selected_color}_{selected_size}"
    
    try:
        # جلب تفاصيل المخزن بدقة
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
            
            request.session[user_cart_key] = cart
            request.session.modified = True
            messages.success(request, f'تمت إضافة المنتج ({selected_color} - {selected_size}) إلى السلة!')
        else:
            messages.warning(request, f"نأسف، المخزن يحتوي على {stock_item.stock} قطع فقط من هذا النوع.")
            
    except ProductSize.DoesNotExist:
        messages.error(request, "عذراً، هذا النوع غير متوفر حالياً.")

    return redirect(request.META.get('HTTP_REFERER', 'shop'))

def cart_view(request):
    user_cart_key = get_user_cart_key(request)
    cart = request.session.get(user_cart_key, {})
    
    if not isinstance(cart, dict):
        cart = {}
        request.session[user_cart_key] = cart

    cart_items = []
    total_price = Decimal('0.00')
    
    for item_key, item_data in cart.items():
        if not isinstance(item_data, dict): continue
            
        try:
            product = Product.objects.get(id=item_data.get('product_id'))
            quantity = item_data.get('quantity', 1)
            # تحديد السعر بناءً على وجود خصم
            price = product.discount_price if product.discount_price else product.price
            subtotal = price * quantity
            total_price += subtotal
            
            variant = ProductVariant.objects.filter(product=product, color_name=item_data.get('color')).first()
            display_image = variant.variant_image.url if variant and variant.variant_image else product.main_image.url
            
            cart_items.append({
                'item_key': item_key,
                'product': product,
                'quantity': quantity,
                'color': item_data.get('color'),
                'size': item_data.get('size', 'N/A'),
                'display_image': display_image,
                'subtotal': subtotal,
                'actual_price': price
            })
        except (Product.DoesNotExist, AttributeError):
            continue
        
    return render(request, 'cart.html', {'cart_items': cart_items, 'total_price': total_price})

def update_cart(request, item_key, action):
    user_cart_key = get_user_cart_key(request)
    cart = request.session.get(user_cart_key, {})
    
    if item_key in cart:
        item_data = cart[item_key]
        if action == 'increase':
            try:
                stock_item = ProductSize.objects.get(
                    variant__product_id=item_data['product_id'],
                    variant__color_name=item_data['color'],
                    size_name=item_data['size']
                )
                if item_data['quantity'] < stock_item.stock:
                    cart[item_key]['quantity'] += 1
                else:
                    messages.warning(request, f"عذراً، لا يوجد سوى {stock_item.stock} قطع في المخزن.")
            except ProductSize.DoesNotExist:
                # التحقق من المخزن العام للمنتج إذا لم توجد تفاصيل مقاسات
                product = get_object_or_404(Product, id=item_data['product_id'])
                if item_data['quantity'] < product.stock:
                    cart[item_key]['quantity'] += 1
                else:
                    messages.warning(request, "تم الوصول للحد الأقصى المتاح في المخزن.")
                
        elif action == 'decrease':
            cart[item_key]['quantity'] -= 1
            if cart[item_key]['quantity'] <= 0: 
                del cart[item_key]
                messages.info(request, "تمت إزالة المنتج من السلة.")
                
        request.session[user_cart_key] = cart
        request.session.modified = True
    else:
        messages.error(request, "تعذر العثور على المنتج في سلتك.")
        
    return redirect('cart_view')

def remove_from_cart(request, item_key):
    user_cart_key = get_user_cart_key(request)
    cart = request.session.get(user_cart_key, {})
    if item_key in cart:
        del cart[item_key]
        request.session[user_cart_key] = cart
        request.session.modified = True
    return redirect('cart_view')

# --- إتمام الشراء (Checkout) ---

def checkout_abuyhia(request):
    if request.user.is_authenticated:
        user_cart_key = f"cart_{request.user.id}"
    else:
        user_cart_key = "cart_guest"
        
    cart = request.session.get(user_cart_key, {})
    
    if not cart:
        messages.warning(request, "سلة المشتريات فارغة!")
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
                messages.error(request, f"عذراً، المتاح فقط {variant_size.stock} من {product.name} ({color_name} - {size_name}).")
                return redirect('cart_view')
        else:
            if product.stock < quantity_requested:
                messages.error(request, f"عذراً، المنتج {product.name} غير متوفر حالياً.")
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
                    <td style="padding: 12px; border-bottom: 1px solid #eee; vertical-align: middle; text-align:right;" dir="rtl">
                        <img src="{img}" width="60" height="60" style="border-radius:8px; margin-left:12px; vertical-align:middle; border:1px solid #ddd; object-fit: cover;">
                        <div style="display: inline-block; vertical-align: middle;">
                            <strong style="font-size: 15px; color: #333;">{product.name}</strong><br>
                            <span style="font-size: 12px; color: #888;">كود: {sku}</span><br>
                            <span style="font-size: 12px; color: #555;">اللون: {color} | المقاس: {size}</span>
                        </div>
                    </td>
                    <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:center;">{qty}</td>
                    <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:left; font-weight: bold;">{int(price_each * qty)} ج.م</td>
                </tr>
            """

            if variant_size:
                variant_size.stock -= qty
                variant_size.save()
            else:
                product.stock -= qty
                product.save()

        html_message = f"""
        <div dir="rtl" style="font-family: 'Segoe UI', Tahoma, Arial, sans-serif; max-width: 600px; margin: auto; border: 1px solid #e2d1b0; border-radius: 15px; overflow: hidden; background-color: #ffffff; text-align: right;">
            <div style="background: linear-gradient(135deg, #c5a059 0%, #b8860b 100%); color: #ffffff; padding: 30px; text-align: center;">
                <h1 style="margin: 0; font-size: 28px; letter-spacing: 1px;">أبو يحيى لتصنيع الأثاث</h1>
                <p style="margin: 5px 0 0; opacity: 0.9;">تأكيد طلب رقم #{order.id}</p>
            </div>
            <div style="padding: 30px;">
                <h2 style="color: #333; margin-top: 0;">أهلاً {name}،</h2>
                <p style="color: #666; line-height: 1.6;">شكراً لثقتك في "أبو يحيى". لقد استلمنا طلبك بنجاح وجاري العمل على تجهيزه وتسليمه لك في أفضل حالة.</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 25px;">
                    <thead>
                        <tr style="background-color: #f9f6f0; border-bottom: 2px solid #c5a059;">
                            <th style="text-align: right; padding: 12px; color: #333;">المنتج</th>
                            <th style="text-align: center; padding: 12px; color: #333;">الكمية</th>
                            <th style="text-align: left; padding: 12px; color: #333;">الإجمالي</th>
                        </tr>
                    </thead>
                    <tbody>{email_items_html}</tbody>
                    <tfoot>
                        <tr>
                            <td colspan="2" style="padding: 20px 10px; text-align: left; font-size: 16px; color: #777;">الإجمالي النهائي:</td>
                            <td style="padding: 20px 0; text-align: left; font-size: 22px; font-weight: bold; color: #c5a059;">{int(total_price)} ج.م</td>
                        </tr>
                    </tfoot>
                </table>
                <div style="margin-top: 30px; padding: 20px; background-color: #fcfaf5; border-radius: 10px; border: 1px solid #f1e9d8;">
                    <h4 style="margin: 0 0 10px 0; color: #b8860b; border-bottom: 1px solid #e2d1b0; padding-bottom: 5px;">بيانات الشحن</h4>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>العنوان:</strong> {address}</p>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>المحافظة:</strong> {governorate}</p>
                    <p style="margin: 5px 0; font-size: 14px; color: #555;"><strong>الهاتف:</strong> {phone}</p>
                </div>
            </div>
            <div style="background-color: #f4f4f4; padding: 15px; text-align: center; font-size: 12px; color: #999;">
                هذه رسالة تلقائية، يرجى عدم الرد عليها مباشرة.<br>
                © 2026 مصنع أبو يحيى للأثاث. جميع الحقوق محفوظة.
            </div>
        </div>
        """

        subject = f"تأكيد طلبك من أبو يحيى للأثاث - رقم #{order.id}"
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

# --- لوحة التحكم والإدارة (Dashboard & Admin) ---

@user_passes_test(is_admin, login_url='login')
def dashboard_view(request):
    orders = Order.objects.all().order_by('-created_at')
    products = Product.objects.all().order_by('-created_at')
    messages_list = ContactMessage.objects.all().order_by('-created_at')
    
    # استخدام التجميع (Aggregation) لحساب الإجمالي بكفاءة
    total_revenue = orders.filter(status='Delivered').aggregate(Sum('total_price'))['total_price__sum'] or 0
    
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
    # ملاحظة: استدعاء VariantFormSet يتطلب استيراده من ملف forms.py
    from .forms import VariantFormSet # استيراد محلي لتجنب التعارض
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        formset = VariantFormSet(request.POST, request.FILES)
        
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                product = form.save()
                for i, v_form in enumerate(formset.forms):
                    if v_form.cleaned_data and not v_form.cleaned_data.get('DELETE', False):
                        variant = v_form.save(commit=False)
                        variant.product = product
                        variant.save()
                        v_form.save_m2m()
                        
                        # معالجة المقاسات الديناميكية
                        size_names = request.POST.getlist(f'size_name_{i}[]')
                        size_quantities = request.POST.getlist(f'size_qty_{i}[]')
                        for name, qty in zip(size_names, size_quantities):
                            if name.strip():
                                ProductSize.objects.create(
                                    variant=variant, size_name=name.strip(),
                                    stock=int(qty) if qty else 0
                                )
                        # معالجة الصور الإضافية
                        extra_images = request.FILES.getlist(f'images_custom_{i}')
                        for img in extra_images:
                            ProductImage.objects.create(variant=variant, image=img)

            messages.success(request, 'تمت إضافة المنتج وجميع المتغيرات بنجاح! ✅')
            return redirect('dashboard')
    else:
        form = ProductForm()
        formset = VariantFormSet()
    
    return render(request, 'manage_product.html', {'form': form, 'formset': formset, 'title': 'Add New Product'})

@user_passes_test(is_admin, login_url='login')
def edit_product(request, pk):
    from .forms import VariantFormSet
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        formset = VariantFormSet(request.POST, request.FILES, instance=product)
        
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                for i, v_form in enumerate(formset.forms):
                    if v_form.cleaned_data and not v_form.cleaned_data.get('DELETE', False):
                        variant = v_form.save(commit=False)
                        variant.product = product
                        variant.save()
                        v_form.save_m2m()
                        
                        # تحديث المقاسات (حذف القديم وإضافة الجديد للتعديل السريع)
                        size_names = request.POST.getlist(f'size_name_{i}[]')
                        size_quantities = request.POST.getlist(f'size_qty_{i}[]')
                        if size_names:
                            variant.sizes.all().delete() # استبدال المقاسات القديمة
                            for name, qty in zip(size_names, size_quantities):
                                if name.strip():
                                    ProductSize.objects.create(
                                        variant=variant, size_name=name.strip(),
                                        stock=int(qty) if qty else 0
                                    )
                        # إضافة صور جديدة
                        for img in request.FILES.getlist(f'images_custom_{i}'):
                            ProductImage.objects.create(variant=variant, image=img)
                    elif v_form.cleaned_data.get('DELETE', False) and v_form.instance.pk:
                        v_form.instance.delete()

            messages.success(request, 'تم تحديث المنتج بنجاح! ✨')
            return redirect('dashboard')
    else:
        form = ProductForm(instance=product)
        formset = VariantFormSet(instance=product)
    
    return render(request, 'manage_product.html', {'form': form, 'formset': formset, 'title': f'Edit: {product.name}'})

@user_passes_test(is_admin, login_url='login')
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.delete()
    messages.error(request, 'تم حذف المنتج بنجاح! 🗑️')
    return redirect('dashboard')

@user_passes_test(is_admin, login_url='login')
def update_order_status(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        new_status = request.POST.get('status')
        old_status = order.status # حفظ الحالة القديمة للمقارنة

        if new_status in dict(Order.STATUS_CHOICES):
            # التأكد من أننا نغير الحالة إلى ملغي ولم يكن ملغياً من قبل (لتجنب تكرار الإرجاع)
            if new_status == 'Canceled' and old_status != 'Canceled':
                with transaction.atomic(): # استخدام الترانزاكشن لضمان سلامة البيانات
                    for item in order.items.all():
                        # محاولة العثور على المقاس المحدد للمنتج
                        variant_size = ProductSize.objects.filter(
                            variant__product=item.product,
                            variant__color_name=item.color,
                            size_name=item.size
                        ).first()

                        if variant_size:
                            variant_size.stock += item.quantity
                            variant_size.save()
                        elif item.product:
                            # إذا لم يكن هناك نظام مقاسات، نعدل مخزن المنتج العام
                            item.product.stock += item.quantity
                            item.product.save()

            order.status = new_status
            order.save() 
            messages.success(request, f'تم تحديث حالة الطلب #{order.id} إلى {new_status}')
        else:
            messages.error(request, 'حالة طلب غير صالحة.')
            
    return redirect('dashboard')

@user_passes_test(is_admin, login_url='login')
def update_item_quantity(request, item_id):
    if request.method == 'POST':
        item = get_object_or_404(OrderItem, id=item_id)
        order = item.order
        action = request.POST.get('action', 'update') 
        product_name = item.product.name if item.product else "منتج"

        if action == 'delete':
            item.delete()
            if not order.items.exists():
                order.status = 'Canceled'
                order.total_price = 0
            else:
                order.total_price = sum(i.quantity * i.price_at_purchase for i in order.items.all())
            order.save()
            messages.success(request, f'تم حذف {product_name} من الطلب.')
            subject, email_content = "تحديث طلبك", f"تمت إزالة {product_name} من طلبك رقم #{order.id}."
        else:
            new_qty = int(request.POST.get('quantity', 1))
            if new_qty > 0:
                item.quantity = new_qty
                item.save()
                order.total_price = sum(Decimal(str(i.quantity * i.price_at_purchase)) for i in order.items.all())
                order.save()
                messages.success(request, f'تم تحديث كمية {product_name}.')
                subject, email_content = "تحديث كمية الطلب", f"تم تحديث الكمية لـ {product_name} في الطلب #{order.id}."
            else:
                messages.error(request, 'الكمية غير صالحة.')
                return redirect('dashboard')

        try: send_mail(subject, email_content, settings.EMAIL_HOST_USER, [order.email], fail_silently=True)
        except Exception: pass
        
    return redirect('dashboard')

@user_passes_test(is_admin, login_url='login')
def apply_order_discount(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        try:
            discount_amount = Decimal(request.POST.get('discount_amount', '0'))
            # حساب الإجمالي الأصلي
            original_total = sum(Decimal(str(i.quantity * i.price_at_purchase)) for i in order.items.all())
            
            if 0 <= discount_amount <= original_total:
                new_total = original_total - discount_amount
                order.total_price = new_total
                order.save()

                # --- بناء صفوف المنتجات للجدول ---
                items_html = ""
                domain = request.get_host()
                protocol = 'https' if request.is_secure() else 'http'

                for item in order.items.all():
                    variant = ProductVariant.objects.filter(product=item.product, color_name=item.color).first()
                    img_path = variant.variant_image.url if variant and variant.variant_image else item.product.main_image.url
                    image_url = f"{protocol}://{domain}{img_path}"
                    
                    items_html += f"""
                    <tr>
                        <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:right;">
                            <img src="{image_url}" width="50" height="50" style="border-radius:5px; vertical-align:middle; margin-left:10px; object-fit:cover;">
                            <span style="color:#333; font-weight:bold;">{item.product.name}</span>
                        </td>
                        <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:center;">{item.quantity}</td>
                        <td style="padding: 12px; border-bottom: 1px solid #eee; text-align:left;">{int(item.quantity * item.price_at_purchase)} ج.م</td>
                    </tr>
                    """

                # --- التصميم الاحترافي للرسالة ---
                html_message = f"""
                <div dir="rtl" style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; border: 1px solid #e2d1b0; border-radius: 15px; overflow: hidden; background-color: #ffffff;">
                    <div style="background: linear-gradient(135deg, #c5a059 0%, #b8860b 100%); color: #ffffff; padding: 25px; text-align: center;">
                        <h2 style="margin: 0;">أبو يحيى لتصنيع الأثاث</h2>
                        <p style="margin: 5px 0 0; opacity: 0.9;">تحديث السعر للطلب #{order.id}</p>
                    </div>
                    
                    <div style="padding: 30px; line-height: 1.6; color: #444; text-align: right;">
                        <h3 style="color: #b8860b;">أهلاً {order.name}،</h3>
                        <p>يسعدنا إبلاغك بأنه تم تطبيق <strong>خصم خاص</strong> على طلبك. أدناه تفاصيل الفاتورة المحدثة:</p>
                        
                        <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
                            <thead>
                                <tr style="background-color: #f9f6f0; color: #333;">
                                    <th style="padding: 10px; text-align: right; border-bottom: 2px solid #c5a059;">المنتج</th>
                                    <th style="padding: 10px; text-align: center; border-bottom: 2px solid #c5a059;">الكمية</th>
                                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #c5a059;">السعر</th>
                                </tr>
                            </thead>
                            <tbody>
                                {items_html}
                            </tbody>
                        </table>

                        <div style="margin-top: 20px; padding: 15px; background-color: #fcfaf5; border-radius: 10px;">
                            <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #777;">
                                <span>الإجمالي الأصلي:</span>
                                <span>{int(original_total)} ج.م</span>
                            </div>
                            <div style="display: flex; justify-content: space-between; margin-bottom: 8px; color: #d9534f; font-weight: bold;">
                                <span>قيمة الخصم:</span>
                                <span>- {int(discount_amount)} ج.م</span>
                            </div>
                            <div style="display: flex; justify-content: space-between; border-top: 1px solid #e2d1b0; pt-10px; margin-top: 10px; font-size: 20px; color: #b8860b; font-weight: bold;">
                                <span>الإجمالي الجديد:</span>
                                <span>{int(new_total)} ج.م</span>
                            </div>
                        </div>

                        <p style="margin-top: 25px; font-size: 14px; color: #666; border-right: 3px solid #c5a059; padding-right: 10px;">
                            سيتم التواصل معكم قريباً لتأكيد موعد التسليم النهائي. شكراً لاختياركم "أبو يحيى".
                        </p>
                    </div>

                    <div style="background-color: #f4f4f4; padding: 15px; text-align: center; font-size: 12px; color: #999;">
                        © 2026 مصنع أبو يحيى للأثاث. جميع الحقوق محفوظة.
                    </div>
                </div>
                """

                subject = f"هدية من أبو يحيى: تم تطبيق خصم على طلبك #{order.id}"
                plain_message = f"تم تطبيق خصم بقيمة {discount_amount} ج.م. الإجمالي الجديد: {new_total} ج.م"

                send_mail(
                    subject=subject,
                    message=plain_message,
                    from_email=settings.EMAIL_HOST_USER,
                    recipient_list=[order.email],
                    html_message=html_message,
                    fail_silently=False  # غيرتها لـ False عشان لو فيه مشكلة في الإعدادات تظهرلك
                )

                messages.success(request, f'تم تطبيق خصم بقيمة {discount_amount} ج.م وإرسال البريد بنجاح.')
            else:
                messages.error(request, 'قيمة الخصم غير منطقية (أكبر من الإجمالي أو أقل من صفر).')
        except Exception as e:
            messages.error(request, f'خطأ في معالجة الخصم: {e}')
            
    return redirect('dashboard')
# --- دوال الحسابات والسياسات ---

def login_view(request):
    if request.method == 'POST':
        u, p = request.POST.get('username'), request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user:
            login(request, user)
            messages.success(request, f'أهلاً بك مجدداً {u}!')
            return redirect('home')
        messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة.')
    return render(request, 'login.html')

def signup_view(request):
    if request.method == 'POST':
        u, e, p = request.POST.get('username'), request.POST.get('email'), request.POST.get('password')
        if User.objects.filter(username=u).exists():
            messages.error(request, 'اسم المستخدم مأخوذ بالفعل.')
        else:
            User.objects.create_user(username=u, email=e, password=p)
            messages.success(request, 'تم إنشاء الحساب! سجل دخولك الآن.')
            return redirect('login')
    return render(request, 'signup.html')

def logout_view(request):
    logout(request)
    messages.info(request, "تم تسجيل الخروج.")
    return redirect('home')

def about_view(request):
    return render(request, 'about.html')

def offers_view(request):
    # تحسين الأداء باستخدام prefetch_related لجلب بيانات الألوان والمقاسات مرة واحدة
    products_list = Product.objects.filter(
        discount_price__gt=0
    ).prefetch_related(
        'variants__sizes', 
        'variants__additional_images'
    ).annotate(
        is_available_group=Case(
            When(stock__gt=0, then=Value(0)), 
            default=Value(1), 
            output_field=IntegerField()
        ),
        manual_new_priority=Case(
            When(is_new_arrival=True, then=Value(1)), 
            default=Value(0), 
            output_field=IntegerField()
        )
    ).order_by('is_available_group', '-manual_new_priority', '-created_at')

    # --- إعداد نظام التقسيم ---
    # عرض 20 منتج فقط في كل صفحة لتقليل وقت التحميل
    paginator = Paginator(products_list, 20) 
    page_number = request.GET.get('page')
    products = paginator.get_page(page_number)

    context = {
        'products': products, 
        'title': 'Exclusive Offers'
    }
    return render(request, 'offers.html', context)

def policies(request):
    return render(request, 'policies.html')

@user_passes_test(is_admin, login_url='login')
def reset_orders(request):
    """حذف جميع الطلبات وتصفير العداد (لأغراض الصيانة فقط)"""
    if request.method == "POST":
        try:
            Order.objects.all().delete()            
            with connection.cursor() as cursor:
                # تصفير عداد الـ ID في قاعدة بيانات SQLite
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='store_order'")
            messages.success(request, "تم حذف جميع الطلبات وتصفير السجل بنجاح.")
        except Exception as e:
            messages.error(request, f"خطأ أثناء المسح: {e}")
    return redirect('dashboard')