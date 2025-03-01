#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name

def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)


def send_bdpu_every_sec():
    global own_bridge_ID, root_bridge_ID, root_path_cost
    global interfaces, vlan_table

    while True:
        # TODO Send BDPU every second if necessary
        # daca switch-ul este root
        if own_bridge_ID==root_bridge_ID:
            for i in interfaces:
                if vlan_table[get_interface_name(i)]=='T':
                    root_bridge_ID=own_bridge_ID
                    sender_bridge_ID=own_bridge_ID
                    sender_path_cost=0

                    # creez cadrul pe care trebuie sa il trimit
                    mac_dst=struct.pack('!6B',0X01,0x80,0xc2,0x00,0x00,0x00)
                    # mac-ul switch-ului
                    mac_src=struct.pack('!6B',*get_switch_mac())
                    # lungimea totala
                    llc_lenght=struct.pack('!H',48)
                    DSAP=0x42
                    SSAP=0x42
                    control=0x03
                    llc_header=struct.pack('!BBB',DSAP,SSAP,control)
                    flags=struct.pack('!B',0)

                    root_bridge_id=struct.pack('!Q',root_bridge_ID)
                    root_cost=struct.pack('!I',sender_path_cost)
                    bridge_id=struct.pack('!Q',sender_bridge_ID)
                    
                    # le pun pe 0 deoarece nu le folosesc 
                    port_id=struct.pack('!H',0)
                    message_age=struct.pack('!H',0)
                    max_age=struct.pack('!H',0)
                    hello_time=struct.pack('!H',0)
                    forward_delay=struct.pack('!H',0)

                    data=mac_dst+mac_src+llc_lenght+llc_header+flags+root_bridge_id+root_cost+bridge_id+port_id+message_age+max_age+hello_time+forward_delay
                    send_to_link(i,len(data),data)

        time.sleep(1)


# verific daca este unicast
# primul bit din stanga din primul octet sa fie 0
def is_unicast(mac):
    valoare = mac[0]+mac[1]
    val_int=int(valoare,16)
    return val_int%2==0

def forwarding_function_vlan(dest_mac,interface,data,length,interfaces,vlan_table,vlan_id,is_Trunk,MAC_table,state_interfaces):
    # data1 nu contine tag-ul
    # data2 contine tag-ul
    data1=data2=data
    length1=length2=length

    # daca interfata pe care a venit este trunk trebuie sa scot tag-ul din pachet pt data1
    # daca interfata pe care a venit nu este trunk trebuie sa creez tag-ul si sa-l adaug in data2
    if is_Trunk:
        # fara tag
        data1=data[0:12]+data[16:]
        length1=length-4
        # cu tag
        data2=data
        length2=length
    else:
        #fara tag
        data1=data
        length1=length
        #cu tag
        data2=data[0:12]+create_vlan_tag(int(vlan_id))+data[12:]
        length2=length+4

    if is_unicast(dest_mac):
        # daca e in tabela
        if dest_mac in MAC_table:
            vlan_tip= vlan_table[get_interface_name(MAC_table[dest_mac])]
            # verif daca e trunk si trimit pachetul care trebuie
            if vlan_tip == 'T' and state_interfaces[MAC_table[dest_mac]]=="LISTENING":
                send_to_link(MAC_table[dest_mac],length2,data2)
            elif int(vlan_tip) == int(vlan_id):
                send_to_link(MAC_table[dest_mac],length1,data1)
        else:
            for i in interfaces:
                if i!=interface and state_interfaces[i]=="LISTENING":
                    vlan_tip = vlan_table[get_interface_name(i)]
                    # verif daca e trunk si trimit pachetul care trebuie
                    if vlan_tip == 'T':
                        send_to_link(i,length2,data2)
                    elif int(vlan_tip) == int(vlan_id):
                        send_to_link(i,length1,data1)
    else:
        for i in interfaces:
            if i!=interface and state_interfaces[i]=="LISTENING":
                vlan_tip = vlan_table[get_interface_name(i)]
                # verif daca e trunk si trimit pachetul care trebuie
                if vlan_tip == 'T':
                    send_to_link(i,length2,data2)
                elif int(vlan_tip) == int(vlan_id):
                    send_to_link(i,length1,data1)


def STP_function(data,interface):
    global own_bridge_ID,root_bridge_ID,root_path_cost
    global interfaces,vlan_table
    global state_interfaces

    # extrag din cadru doar informatiile pe care le folosesc
    BPDU_root_bridge_ID=int.from_bytes(data[18:26], byteorder='big')
    BPDU_sender_path_cost=int.from_bytes(data[26:30], byteorder='big')
    BPDU_sender_bridge_ID=int.from_bytes(data[30:38], byteorder='big')
    root_port=interface
    root_bridge_ID_old=root_bridge_ID
            
    if(BPDU_root_bridge_ID<root_bridge_ID):
        root_bridge_ID=BPDU_root_bridge_ID
        root_path_cost=BPDU_sender_path_cost+10
        root_port=interface
                
        # we_were_the_root
        if own_bridge_ID==root_bridge_ID_old:
            for i in interfaces:
                if i!=root_port and vlan_table[get_interface_name(i)]=='T':
                    state_interfaces[i]="BLOCKING"
                
        if state_interfaces[root_port]=="BLOCKING":
            state_interfaces="LISTENING"

        # construiesc cadrul pe care trebuie sa il trimit cu noul sender_bridge_ID care este own_bridge_ID si sender_path_cost care este root_path_cost
        mac_dst=struct.pack('!6B',0X01,0x80,0xc2,0x00,0x00,0x00)
        # mac-ul switch-ului
        mac_src=struct.pack('!6B',*get_switch_mac())
        llc_lenght=struct.pack('!H',48)
        DSAP=0x42
        SSAP=0x42
        control=0x03
        llc_header=struct.pack('!BBB',DSAP,SSAP,control)
        flags=struct.pack('!B',0)

        root_bridge_id=struct.pack('!Q',root_bridge_ID)
        #  sender_path_cost este root_path_cost
        root_cost=struct.pack('!I',root_path_cost)
        # sender_bridge_ID este own_bridge_ID
        bridge_id=struct.pack('!Q',own_bridge_ID)

        # le pun pe 0 deoarece nu le folosesc 
        port_id=struct.pack('!H',0)
        message_age=struct.pack('!H',0)
        max_age=struct.pack('!H',0)
        hello_time=struct.pack('!H',0)
        forward_delay=struct.pack('!H',0)

        data=mac_dst+mac_src+llc_lenght+llc_header+flags+root_bridge_id+root_cost+bridge_id+port_id+message_age+max_age+hello_time+forward_delay

        # trimit valorile actualizate
        for i in interfaces:
            if i!=root_port and vlan_table[get_interface_name(i)]=='T':
                send_to_link(i,len(data),data)

    elif BPDU_sender_bridge_ID==root_bridge_ID:
        if interface==root_port and BPDU_sender_path_cost+10<root_path_cost:
            root_path_cost=BPDU_sender_path_cost+10

        elif interface!=root_port:
            if BPDU_sender_path_cost>root_path_cost:
                state_interfaces[interface]="LISTENING"
            

    elif BPDU_sender_bridge_ID==own_bridge_ID:
        state_interfaces[interface]="BLOCKING"

    if own_bridge_ID== root_bridge_ID:
        for i in interfaces:
            if vlan_table[get_interface_name(i)]=='T':
                state_interfaces[i]="LISTENING"

def main():

    global interfaces,vlan_table
    global own_bridge_ID,root_bridge_ID,root_path_cost
    global state_interfaces

    MAC_table={}
    vlan_table={}
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    switch_id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)
    
    # citesc fisierul de configurare pentru switch
    # compun numele fisierului
    name = 'configs/switch'+switch_id + '.cfg'
    f=open(name,'r')
    # prima data citesc prioritatea switch-ului
    switch_priority=f.readline().strip()

    # citesc restul de linii pentru configurarea switch-ului
    for line in f:
        line=line.strip()
        atribute=line.split()
        vlan_table[atribute[0]]=atribute[1]
        
    # initiarizare BPDU
    state_interfaces=["BLOCKING"]*num_interfaces
    switch_priority=int(switch_priority)
    own_bridge_ID =switch_priority
    root_bridge_ID=own_bridge_ID
    root_path_cost=0

    # nu mai verific sa fie trunk deoarece si cele care nu sunt trunk trebuie sa fie pe LISTENING initial
    if own_bridge_ID==root_bridge_ID:
        for i in interfaces:
            state_interfaces[i]="LISTENING"

    print("# Starting switch with id {}".format(switch_id), flush=True)
    print("[INFO] Switch MAC", ':'.join(f'{b:02x}' for b in get_switch_mac()))

    # Create and start a new thread that deals with sending BDPU
    t = threading.Thread(target=send_bdpu_every_sec)
    t.start()

    # Printing interface names
    for i in interfaces:
        print(get_interface_name(i))

    while True:
        # Note that data is of type bytes([...]).
        # b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
        # b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
        # b3 = b1[0:2] + b[3:4].
        interface, data, length = recv_from_any_link()

        dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

        # Print the MAC src and MAC dst in human readable format
        dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
        src_mac = ':'.join(f'{b:02x}' for b in src_mac)

        # Note. Adding a VLAN tag can be as easy as
        # tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]

        print(f'Destination MAC: {dest_mac}')
        print(f'Source MAC: {src_mac}')
        print(f'EtherType: {ethertype}')

        print("Received frame of size {} on interface {}".format(length, interface), flush=True)

        # dest_mac = data[0:6]
        # src_mac = data[6:12]

        # if dest_mac == b'\xff\xff\xff\xff\xff\xff'

        # TODO: Implement forwarding with learning
        # TODO: Implement VLAN support
        # TODO: Implement STP support

        # verific adresa mac dest pentru a vedea daca este pentru BPDU      
        mac_BPDU=data[0:6]
        if mac_BPDU==b'\x01\x80\xc2\x00\x00\x00':
            STP_function(data,interface)
            continue


        # retin in tabela
        MAC_table[src_mac]=interface

        vlan_tip=vlan_table[get_interface_name(interface)]

        # retin daca este trunk
        is_Trunk=False
        if(vlan_tip=='T'):
            is_Trunk=True
        
        if vlan_id==-1:
            vlan_id=vlan_table[get_interface_name(interface)]
         
        forwarding_function_vlan(dest_mac,interface,data,length,interfaces,vlan_table,vlan_id,is_Trunk,MAC_table,state_interfaces)

        # data is of type bytes.
        # send_to_link(i, length, data)

if __name__ == "__main__":
    main()
