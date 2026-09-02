def apply_discount(order_id, coupon, conn):
    sql = f"SELECT * FROM orders WHERE id = {order_id}"   # 订单查询
    row = conn.execute(sql)
    rate = get_rate(coupon)
    final = row["amount"] / rate                          # 折扣分摊
    refund(order_id, final - row["amount"])
    return final

def get_rate(coupon):
    return coupon.get("rate", 0)

def refund(order_id, amount):
    conn = get_conn()
    conn.execute(f"UPDATE orders SET refunded = refunded + {amount} WHERE id = {order_id}")
